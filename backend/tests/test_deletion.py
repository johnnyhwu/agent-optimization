"""Deletion order.

`question_results.question_pk -> questions.id` has no ON DELETE CASCADE (§9.4,
deliberate: a locked set never deletes questions), so deleting an eval set by
cascade alone would rely on Postgres happening to remove `question_results`
before `questions` — an ordering it does not promise. These tests pin the
explicit deepest-first order, because the failure mode is a foreign-key error on
a real database that no DB-free test would otherwise catch.
"""
from __future__ import annotations

import uuid

from sqlalchemy import delete as sa_delete

from app.models import (
    EvalSet,
    EvalSetRole,
    EvalSetScript,
    OptimizationRollout,
    OptimizationStep,
    Question,
    QuestionResult,
    QuestionSkill,
    Run,
    SpanAnalysis,
)
from app.services.deletion import (
    delete_eval_set,
    delete_optimization_run,
    delete_run,
)


class RecordingSession:
    """Records which table each DELETE targets, in order."""

    def __init__(self, ids: dict) -> None:
        self._ids = ids
        self.deleted: list[str] = []

    async def scalars(self, statement):
        # The service only selects id columns; key off the entity it came from.
        entity = statement.column_descriptions[0]["entity"]
        return _Scalars(self._ids.get(entity.__tablename__, []))

    async def execute(self, statement):
        assert isinstance(statement, type(sa_delete(Run))), "only DELETEs expected"
        self.deleted.append(statement.table.name)
        return None


class _Scalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return list(self._items)


def _ids(n: int) -> list[uuid.UUID]:
    return [uuid.uuid4() for _ in range(n)]


async def test_delete_run_removes_children_before_the_run():
    session = RecordingSession({QuestionResult.__tablename__: _ids(2)})
    await delete_run(session, uuid.uuid4())
    assert session.deleted == ["span_analyses", "question_results", "runs"]


async def test_delete_run_with_no_results_still_drops_the_run():
    session = RecordingSession({QuestionResult.__tablename__: []})
    await delete_run(session, uuid.uuid4())
    # No span_analyses statement when there is nothing to hang off.
    assert session.deleted == ["question_results", "runs"]


async def test_delete_eval_set_order_is_deepest_first():
    session = RecordingSession(
        {
            Run.__tablename__: _ids(2),
            QuestionResult.__tablename__: _ids(4),
            Question.__tablename__: _ids(3),
        }
    )
    await delete_eval_set(session, uuid.uuid4())

    assert session.deleted == [
        "span_analyses",
        "question_results",
        "runs",
        "question_skills",
        "questions",
        "eval_set_roles",
        "eval_set_scripts",
        "eval_sets",
    ]
    # The rule the FK actually enforces: question_results must be gone before
    # questions, and that is not something cascade ordering guarantees.
    assert session.deleted.index("question_results") < session.deleted.index("questions")


async def test_deleting_an_optimization_run_works_down_from_the_leaves():
    """One run is tens of thousands of rows, and the order they go in is the part
    that can be got wrong silently.

    `session.delete(run)` would also work — every foreign key below the run
    cascades — but it loads the entire tree into memory to do it: a row object
    per question per rollout per step. These are nine statements instead, and
    the order is explicit rather than left to however Postgres happens to walk
    the cascade.
    """
    session = RecordingSession(
        {
            OptimizationStep.__tablename__: _ids(3),
            OptimizationRollout.__tablename__: _ids(6),
        }
    )
    await delete_optimization_run(session, uuid.uuid4())

    assert session.deleted == [
        "optimization_results",
        "optimization_rollouts",
        "optimization_stage_calls",
        "optimization_minibatches",
        "optimization_steps",
        "optimization_items",
        "optimization_skills",
        "optimization_runs",
    ]
    # Nothing on the evaluation side is touched: the links that cross over are
    # ON DELETE SET NULL, and a run must not take questions with it.
    assert not {"questions", "question_results", "runs", "eval_sets"} & set(session.deleted)


async def test_deleting_a_run_that_never_ran_a_step_still_drops_the_run():
    """A run cancelled while pending has items and no steps at all."""
    session = RecordingSession({OptimizationStep.__tablename__: []})
    await delete_optimization_run(session, uuid.uuid4())
    assert session.deleted == [
        "optimization_items",
        "optimization_skills",
        "optimization_runs",
    ]


async def test_a_step_with_no_rollouts_still_takes_its_stage_calls():
    """An aborted step has stage calls and no rollout — the analyst was called
    and the run died before anything was scored."""
    session = RecordingSession(
        {OptimizationStep.__tablename__: _ids(1), OptimizationRollout.__tablename__: []}
    )
    await delete_optimization_run(session, uuid.uuid4())
    assert session.deleted == [
        "optimization_stage_calls",
        "optimization_minibatches",
        "optimization_steps",
        "optimization_items",
        "optimization_skills",
        "optimization_runs",
    ]


async def test_delete_eval_set_with_no_runs_skips_run_children():
    session = RecordingSession(
        {Run.__tablename__: [], Question.__tablename__: _ids(1)}
    )
    await delete_eval_set(session, uuid.uuid4())
    assert session.deleted == [
        "question_skills",
        "questions",
        "eval_set_roles",
        "eval_set_scripts",
        "eval_sets",
    ]


async def test_the_stored_script_goes_with_the_set_that_produced_it():
    """A set built from a Python script keeps that script for provenance; deleting
    the set must take it with it, or the source of a deleted set outlives it."""
    session = RecordingSession({Question.__tablename__: _ids(1)})
    await delete_eval_set(session, uuid.uuid4())
    assert "eval_set_scripts" in session.deleted
    assert session.deleted.index("eval_set_scripts") < session.deleted.index("eval_sets")


def test_every_child_table_is_covered():
    """A new child table added to the schema without a line in deletion.py would
    otherwise only be found by a foreign-key error in production."""
    covered = {
        SpanAnalysis.__tablename__,
        QuestionResult.__tablename__,
        Run.__tablename__,
        QuestionSkill.__tablename__,
        Question.__tablename__,
        EvalSetRole.__tablename__,
        EvalSetScript.__tablename__,
        EvalSet.__tablename__,
        # Optimize's tables are deleted by `delete_optimization_run`, which is a
        # separate entry point on purpose: they are not children of an eval set.
        # They reference `questions` and `eval_sets` with ON DELETE SET NULL and
        # every row that needs a question carries its own snapshot of the text,
        # so an optimization run outlives the sets it drew from — deleting a set
        # next month must leave last month's run readable rather than delete it.
        # `test_optimizer_isolation.py` proves that half over real tables.
        "optimization_runs",
        "optimization_items",
        "optimization_steps",
        "optimization_rollouts",
        "optimization_results",
        "optimization_minibatches",
        "optimization_stage_calls",
        "optimization_skills",
    }
    from app.db import Base

    assert set(Base.metadata.tables) == covered
