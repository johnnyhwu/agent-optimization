"""Optimize must be additive: nothing it stores may change what Evaluation says.

An optimization run performs a *lot* of rollouts — epochs × steps × (train + val)
agent calls, each with a verdict. That is the same shape as eval data, and it
lands in the same database. If any of it leaked into the endpoints Evaluation
already has, the damage would be quiet and severe:

  * an eval set's card would count optimization rollouts among its runs,
  * its pass-rate sparkline and regression summary would move for reasons that
    have nothing to do with the agent, and
  * `docs/spec.md` §10.2③ records that `GET /eval-sets` was deliberately
    rewritten to touch a bounded number of rows — a new table joined into that
    query would undo it.

So Optimize gets its own tables and its own endpoints, and this file is the
guard. The structural half runs everywhere; the behavioural half needs a real
database and skips without `TEST_DATABASE_URL`, exactly like the paging and
reaper tests it sits beside:

    TEST_DATABASE_URL='postgresql+asyncpg://localhost/agenteval_test' \\
        pytest tests/test_optimizer_isolation.py
"""
from __future__ import annotations

import ast
import os
import pathlib
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.main import reap_interrupted_runs
from app.models import (
    EvalSet,
    EvalSetRole,
    OptimizationItem,
    OptimizationResult,
    OptimizationRollout,
    OptimizationRun,
    OptimizationStep,
    Question,
    QuestionResult,
    Run,
)
from app.routers.eval_sets import list_eval_sets
from app.routers.runs import list_runs

MIGRATION = (
    pathlib.Path(__file__).resolve().parents[1]
    / "alembic" / "versions" / "0009_optimization.py"
)

# The tables that existed before Optimize. Nothing in the 0009 migration may
# touch one of them.
PRE_EXISTING_TABLES = {
    "eval_sets", "eval_set_scripts", "questions", "question_skills", "runs",
    "question_results", "span_analyses", "eval_set_roles", "alembic_version",
}


# --- Structural: the migration is additive ---------------------------------


def _upgrade_calls() -> list[tuple[str, str | None]]:
    """`(op_name, first_string_arg)` for every `op.*` call in `upgrade()`."""
    tree = ast.parse(MIGRATION.read_text())
    fn = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "upgrade"
    )
    calls: list[tuple[str, str | None]] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if not (isinstance(node.func.value, ast.Name) and node.func.value.id == "op"):
            continue
        first = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else None
        calls.append((node.func.attr, first if isinstance(first, str) else None))
    return calls


def test_the_migration_only_creates_things():
    """0009 must not alter, drop or rename anything that already existed.

    This is the cheapest possible statement of "Optimize is additive", and it
    runs without a database. An `add_column` on `runs` would be a perfectly
    reasonable-looking way to hang an optimization flag off the existing table —
    and the first thing to make an eval set's history ambiguous.
    """
    forbidden = {
        "add_column", "alter_column", "drop_column", "drop_table",
        "drop_constraint", "rename_table", "execute",
    }
    used = {op for op, _ in _upgrade_calls()}

    assert not (used & forbidden), f"0009 must be create-only, found: {sorted(used & forbidden)}"


def test_the_migration_never_names_a_pre_existing_table():
    """Belt and braces: no `op.*` call in 0009 may name an old table.

    `create_index` on `runs`, say, is not destructive but it is still a change to
    a table whose query plans were tuned deliberately (§10.2③).
    """
    named = {name for _, name in _upgrade_calls() if name}

    assert not (named & PRE_EXISTING_TABLES), (
        f"0009 touches pre-existing tables: {sorted(named & PRE_EXISTING_TABLES)}"
    )


def _created_tables() -> dict[str, set[str]]:
    """`{table: {column names}}` as the migration declares them."""
    tree = ast.parse(MIGRATION.read_text())
    fn = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "upgrade"
    )
    tables: dict[str, set[str]] = {}
    for node in ast.walk(fn):
        is_create = (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create_table"
        )
        if not is_create or not node.args:
            continue
        name = node.args[0].value
        columns: set[str] = set()
        for arg in node.args[1:]:
            if not isinstance(arg, ast.Call):
                continue
            # sa.Column("x", ...) directly, or a helper like _pk() / _json("x").
            if isinstance(arg.func, ast.Attribute) and arg.func.attr == "Column":
                if arg.args and isinstance(arg.args[0], ast.Constant):
                    columns.add(arg.args[0].value)
            elif isinstance(arg.func, ast.Name) and arg.func.id == "_pk":
                columns.add("id")
            elif isinstance(arg.func, ast.Name) and arg.func.id == "_json":
                if arg.args and isinstance(arg.args[0], ast.Constant):
                    columns.add(arg.args[0].value)
        tables[name] = columns
    return tables


def test_the_migration_and_the_models_describe_the_same_schema():
    """Production runs the migration; the tests run `Base.metadata.create_all`.

    Those are two independent descriptions of the same tables, and when they
    drift the tests keep passing against a schema that does not exist anywhere
    else — the failure surfaces as a 500 on the deployed box, from code that had
    green tests. Column *names* are the drift that actually happens (a field
    added to the model and forgotten in the migration), so that is what is
    compared.
    """
    declared = _created_tables()

    for model in (
        OptimizationRun, OptimizationItem, OptimizationStep,
        OptimizationRollout, OptimizationResult,
    ):
        name = model.__tablename__
        assert name in declared, f"{name} is in models.py but not in migration 0009"
        assert declared[name] == set(model.__table__.columns.keys()), (
            f"{name}: migration and model disagree — "
            f"only in migration {sorted(declared[name] - set(model.__table__.columns.keys()))}, "
            f"only in model {sorted(set(model.__table__.columns.keys()) - declared[name])}"
        )


def test_optimization_tables_do_not_collide_with_existing_ones():
    new = {
        OptimizationRun.__tablename__, OptimizationItem.__tablename__,
        OptimizationStep.__tablename__, OptimizationRollout.__tablename__,
        OptimizationResult.__tablename__,
    }

    assert not (new & PRE_EXISTING_TABLES)
    assert all(name.startswith("optimization_") for name in new)


def test_no_optimization_relationship_is_hung_off_an_existing_model():
    """A `back_populates` onto EvalSet or Run would change their loading.

    Adding `EvalSet.optimization_runs` is the natural-looking thing to do and it
    would put a new collection on the very object `_build_cards` reads a page of.
    Optimize points *at* those tables by id and they do not point back.
    """
    for model in (EvalSet, Run, Question, QuestionResult):
        attrs = set(model.__mapper__.relationships.keys())
        assert not any("optimization" in a for a in attrs), (
            f"{model.__name__} grew an optimization relationship: {attrs}"
        )


# --- Behavioural: the endpoints do not see optimization rows ---------------

TEST_DB = os.environ.get("TEST_DATABASE_URL")
db_only = pytest.mark.skipif(
    not TEST_DB, reason="set TEST_DATABASE_URL to run the database-backed isolation tests"
)


@pytest.fixture
async def factory():
    engine = create_async_engine(TEST_DB)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def seed_eval_set(session, *, subject="alice", runs=2, questions=2):
    es = EvalSet(name="billing", source_format="jsonl", meta={})
    session.add(es)
    await session.flush()
    session.add(EvalSetRole(eval_set_id=es.id, user_subject=subject, role="owner"))

    qs = []
    for i in range(questions):
        q = Question(
            eval_set_id=es.id, question_id=f"q_{i}", question="q",
            ground_truth_response="gt", ground_truth_reasoning="r",
        )
        session.add(q)
        await session.flush()
        qs.append(q)

    for r in range(runs):
        run = Run(
            eval_set_id=es.id, triggered_by=subject, name=f"run {r}",
            status="completed", config={}, secrets={},
            pass_rate=0.5, total_count=questions, correct_count=questions // 2,
        )
        session.add(run)
        await session.flush()
        for i, q in enumerate(qs):
            session.add(QuestionResult(
                run_id=run.id, question_pk=q.id, correlation_id=uuid.uuid4().hex,
                verdict="correct" if i % 2 == 0 else "incorrect",
                status="done", trace_ready=True,
            ))
    await session.commit()
    return es, qs


async def seed_optimization(session, es, questions, *, subject="alice", rollouts=6):
    """An optimization run with more rollout results than the eval set has runs.

    Deliberately lopsided: if any of this leaked into the eval-set card, the
    numbers would not merely shift, they would be dominated by it.
    """
    orun = OptimizationRun(
        name="opt", created_by=subject, status="completed", mode="isolated",
        skill_name="billing", config={}, secrets={},
        initial_skill={"billing/SKILL.md": "# Billing\n"},
        num_epochs=2, batch_size=2, steps_per_epoch=1, total_steps=2,
    )
    session.add(orun)
    await session.flush()

    for split in ("train", "val"):
        for ordinal, q in enumerate(questions):
            session.add(OptimizationItem(
                run_id=orun.id, split=split, question_pk=q.id,
                source_eval_set_id=es.id, item_key=f"{es.id}:{q.question_id}",
                question=q.question, ground_truth_response=q.ground_truth_response,
                ground_truth_reasoning=q.ground_truth_reasoning, ordinal=ordinal,
            ))

    for step_no in range(2):
        step = OptimizationStep(
            run_id=orun.id, step_no=step_no, epoch_no=step_no + 1, step_in_epoch=0,
            status="done", gate_action="accept_new_best",
        )
        session.add(step)
        await session.flush()
        for split in ("train", "val"):
            rollout = OptimizationRollout(
                step_id=step.id, split=split, skill_step_no=step_no,
                n_items=rollouts, n_scored=rollouts, hard=1.0, soft=1.0,
            )
            session.add(rollout)
            await session.flush()
            for i in range(rollouts):
                session.add(OptimizationResult(
                    rollout_id=rollout.id, item_key=f"{es.id}:q_{i}",
                    question_pk=questions[i % len(questions)].id,
                    correlation_id=uuid.uuid4().hex, verdict="correct",
                    status="done", trace_ready=True,
                ))
    await session.commit()
    return orun


@db_only
async def test_eval_set_cards_are_identical_with_and_without_optimization_data():
    """The home page must read the same before and after an optimization run.

    Run counts, latest pass rate, the sparkline and the regression summary are
    all computed from `runs` / `question_results`. This asserts the whole payload
    is byte-identical, rather than picking fields to check — a later join would
    show up in whichever field nobody thought to assert.
    """
    engine = create_async_engine(TEST_DB)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as session:
        es, qs = await seed_eval_set(session)
        before = await list_eval_sets(
            q=None, metadata_key=None, metadata_value=None, sort="created_at",
            limit=24, offset=0, subject="alice", session=session,
        )
        before_dump = before.model_dump() if hasattr(before, "model_dump") else before

    async with maker() as session:
        await seed_optimization(session, es, qs)

    async with maker() as session:
        after = await list_eval_sets(
            q=None, metadata_key=None, metadata_value=None, sort="created_at",
            limit=24, offset=0, subject="alice", session=session,
        )
        after_dump = after.model_dump() if hasattr(after, "model_dump") else after

    await engine.dispose()
    assert after_dump == before_dump


@db_only
async def test_run_history_is_identical_with_and_without_optimization_data():
    """`GET /eval-sets/{id}/runs` counts runs of *this set*, not rollouts."""
    engine = create_async_engine(TEST_DB)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as session:
        es, qs = await seed_eval_set(session)
        before = await list_runs(
            eval_set_id=es.id, q=None, limit=20, offset=0,
            subject="alice", session=session,
        )
        before_dump = before.model_dump() if hasattr(before, "model_dump") else before

    async with maker() as session:
        await seed_optimization(session, es, qs)

    async with maker() as session:
        after = await list_runs(
            eval_set_id=es.id, q=None, limit=20, offset=0,
            subject="alice", session=session,
        )
        after_dump = after.model_dump() if hasattr(after, "model_dump") else after

    await engine.dispose()
    assert after_dump == before_dump


@db_only
async def test_deleting_a_source_eval_set_leaves_the_optimization_run_readable(factory):
    """An optimization run outlives the eval sets it drew questions from.

    It belongs to no single set — it can import from several — so it is not a
    child of any of them, which is why `deletion.py` has no line for these tables
    and `test_deletion.py` lists them as deliberately uncovered. That claim needs
    proving over real tables rather than asserting, because it rests on two
    things a DB-free test cannot see: that `ON DELETE SET NULL` actually fires
    when `delete_eval_set` removes questions with an explicit DELETE, and that
    nothing raises a foreign-key error on the way.

    The snapshot columns are what make the survivor still readable. Without them
    a run whose source set was deleted would render as a list of blank rows.
    """
    from app.services.deletion import delete_eval_set

    async with factory() as session:
        es, qs = await seed_eval_set(session, runs=1)
        run = await seed_optimization(session, es, qs, rollouts=2)
        run_id, es_id = run.id, es.id

    async with factory() as session:
        await delete_eval_set(session, es_id)
        await session.commit()

    async with factory() as session:
        survivor = await session.get(OptimizationRun, run_id)
        assert survivor is not None, "deleting a source set must not delete the run"

        items = (
            await session.scalars(
                select(OptimizationItem).where(OptimizationItem.run_id == run_id)
            )
        ).all()
        assert items, "the dataset snapshot must survive its source"
        for item in items:
            assert item.question, "the question text is snapshotted, not joined"
            assert item.question_pk is None, "the dangling link is nulled, not left broken"
            assert item.source_eval_set_id is None


@db_only
async def test_the_existing_reaper_leaves_optimization_runs_alone(factory):
    """A backend restart must not turn a resumable optimization run into a failure.

    `reap_interrupted_runs` closes out `runs` stuck in 'running' (`app/main.py`),
    which is right for an eval: it is unresumable, so 'failed' is the honest
    end state. An optimization run is checkpointed per step and *is* resumable,
    so it gets 'interrupted' and a Resume button from its own reaper. Generalising
    the existing reaper over both tables would silently throw away an hour of
    agent calls on every deploy.
    """
    async with factory() as session:
        es = EvalSet(name="set", source_format="jsonl", meta={})
        session.add(es)
        await session.flush()
        session.add(Run(eval_set_id=es.id, triggered_by="alice", status="running",
                        config={}, secrets={}))
        orun = OptimizationRun(
            name="opt", created_by="alice", status="running", mode="isolated",
            skill_name="billing", config={}, secrets={},
            initial_skill={"billing/SKILL.md": "# Billing\n"},
            num_epochs=1, batch_size=1, steps_per_epoch=1, total_steps=1,
        )
        session.add(orun)
        await session.commit()
        orun_id = orun.id

    closed = await reap_interrupted_runs(session_factory=factory)

    assert closed == 1, "only the eval run should have been closed out"
    async with factory() as session:
        assert (await session.get(OptimizationRun, orun_id)).status == "running"
