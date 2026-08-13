"""The warning that a step memorised an answer instead of learning a rule.

The reflect stage is shown each failed item's gold answer — it has to be, that
is how it works out *why* the agent was wrong — so the optimizer is perfectly
capable of writing "when asked about ACME Q2, answer $42,180.00" into the skill.
Training accuracy jumps, the run looks like a success, and the skill is worth
nothing on any question nobody thought to ask.

Three defences exist. The analyst prompt forbids it, which is a request. The
held-out validation split is the structural one. This is the visible one: the
diff is read by a person, and a memorised answer is obvious there the moment it
is pointed at — which only happens if something points.

`skillio.find_answer_leaks` is unit-tested in `test_optimizer_skill_ops.py`.
What is tested here is the wiring nothing else covers: which answers get
searched, and whether the page ever sees them.
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models import (
    EvalSet,
    EvalSetRole,
    OptimizationItem,
    OptimizationRun,
    OptimizationSkill,
    OptimizationStep,
)
from app.optimizer import skillio
from app.routers import optimization as opt

TEST_DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="set TEST_DATABASE_URL to run the database-backed leak tests"
)

GOLD_TRAIN = "Refunds settle in exactly 4 business days."
GOLD_VAL = "Escalations are answered within 90 minutes."
INITIAL = {"billing/SKILL.md": "# Billing\n\nAnswer refund questions.\n"}


@pytest.fixture
async def engine():
    eng = create_async_engine(TEST_DB)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()
    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(text(f'TRUNCATE TABLE "{table.name}" CASCADE'))


async def make_run(session, *, golds=((("train", GOLD_TRAIN)), ("val", GOLD_VAL))):
    eval_set = EvalSet(name="set", source_format="jsonl", meta={})
    session.add(eval_set)
    await session.flush()
    session.add(EvalSetRole(eval_set_id=eval_set.id, user_subject="alice", role="owner"))

    run = OptimizationRun(
        name="tune billing", created_by="alice", status="completed",
        mode="isolated", skill_name="billing", config={}, secrets={},
        initial_skill=dict(INITIAL), detector={},
        num_epochs=1, batch_size=2, steps_per_epoch=2, total_steps=2, best_step=0,
    )
    session.add(run)
    await session.flush()
    for i, (split, gold) in enumerate(golds):
        session.add(OptimizationItem(
            run_id=run.id, split=split, item_key=f"{eval_set.id}:q{i}", question_pk=None,
            source_eval_set_id=eval_set.id, question=f"question {i}",
            ground_truth_response=gold, ground_truth_reasoning="because", ordinal=i,
        ))
    session.add(OptimizationSkill(
        run_id=run.id, step_no=0, kind="initial", files=dict(INITIAL),
        content_hash="hash-0", per_file_stats={},
    ))
    await session.commit()
    return run


async def add_step(session, run, files, *, step_no=1, parent_step_no=None):
    session.add(OptimizationStep(
        run_id=run.id, step_no=step_no, epoch_no=1, step_in_epoch=step_no,
        status="done", parent_step_no=parent_step_no, gate_action="accept_new_best",
    ))
    session.add(OptimizationSkill(
        run_id=run.id, step_no=step_no, kind="candidate", files=dict(files),
        content_hash=f"hash-{step_no}", per_file_stats={},
    ))
    await session.commit()


async def diff(session, run, step_no=1, base="parent"):
    return await opt.get_step_skill_diff(
        run.id, step_no, base=base, subject="alice", session=session
    )


# --- The warning fires ------------------------------------------------------


async def test_a_gold_answer_written_into_the_skill_is_flagged_with_its_line(session):
    """The whole failure this section exists for, end to end.

    Without it the diff shows a plausible-looking new rule, the chart shows
    training accuracy climbing, and nothing on any screen distinguishes a skill
    that learned the refund policy from one that learned this one question's
    answer. The line is quoted because the path alone sends a reader hunting
    through a file for text they have not been shown.
    """
    run = await make_run(session)
    leaked = {
        "billing/SKILL.md": INITIAL["billing/SKILL.md"]
        + f"If asked how long a refund takes: {GOLD_TRAIN}\n"
    }
    await add_step(session, run, leaked)

    view = await diff(session, run)
    assert len(view.answer_leaks) == 1
    leak = view.answer_leaks[0]
    assert leak.path == "billing/SKILL.md"
    assert leak.answer == GOLD_TRAIN
    assert leak.line == f"If asked how long a refund takes: {GOLD_TRAIN}"


async def test_a_leak_in_a_reference_file_is_found_too(session):
    """`append` to a new path is the cheapest place to hide one.

    A run that has learned it cannot edit `SKILL.md` freely — routing mode, or a
    protected region — will write to a reference document instead, and the agent
    reads those. Searching only the entry point would miss the case the
    protection rules push the optimizer towards.
    """
    run = await make_run(session)
    leaked = {**INITIAL, "billing/references/faq.md": f"Refund timing: {GOLD_TRAIN}\n"}
    await add_step(session, run, leaked)

    view = await diff(session, run)
    assert [leak.path for leak in view.answer_leaks] == ["billing/references/faq.md"]


async def test_a_step_that_leaked_nothing_reports_no_warning(session):
    """A warning that fires on ordinary edits is ignored within a day.

    This is the case that decides whether the other tests are worth anything: if
    the check flagged every step, "no leaks" would carry no information and the
    banner would be scrolled past on the step that mattered.
    """
    run = await make_run(session)
    clean = {
        "billing/SKILL.md": INITIAL["billing/SKILL.md"]
        + "State the refund window from the policy table before answering.\n"
    }
    await add_step(session, run, clean)

    view = await diff(session, run)
    assert view.answer_leaks == []


# --- Which answers are searched, and against what -----------------------------


async def test_only_the_answers_this_step_added_are_flagged(session):
    """A run that leaked once must not flag every step after it.

    The leaked line stays in the skill, so a check that searched the whole
    candidate would re-report it on step 2, step 3 and step 4 — burying the step
    that actually introduced it and making the warning useless for finding out
    *when* the run went wrong.
    """
    run = await make_run(session)
    leaked = {
        "billing/SKILL.md": INITIAL["billing/SKILL.md"]
        + f"If asked how long a refund takes: {GOLD_TRAIN}\n"
    }
    await add_step(session, run, leaked, step_no=1)
    later = {**leaked, "billing/SKILL.md": leaked["billing/SKILL.md"] + "Be brief.\n"}
    await add_step(session, run, later, step_no=2, parent_step_no=1)

    assert len((await diff(session, run, 1)).answer_leaks) == 1
    assert (await diff(session, run, 2)).answer_leaks == []


async def test_the_leak_reappears_when_the_diff_is_taken_against_the_initial_skill(session):
    """The same step, the same skill, a different question being asked.

    "vs previous" answers "what did this step do"; "vs initial" answers "what
    does this run's skill contain that the original did not". A memorised answer
    introduced at step 1 is still in the skill at step 4, and the second view is
    where a reader looks before deploying it. Computing the leaks once against
    the parent and reusing them for both bases would make that view quietly
    blind.
    """
    run = await make_run(session)
    leaked = {
        "billing/SKILL.md": INITIAL["billing/SKILL.md"]
        + f"If asked how long a refund takes: {GOLD_TRAIN}\n"
    }
    await add_step(session, run, leaked, step_no=1)
    later = {**leaked, "billing/SKILL.md": leaked["billing/SKILL.md"] + "Be brief.\n"}
    await add_step(session, run, later, step_no=2, parent_step_no=1)

    view = await diff(session, run, 2, base="initial")
    assert [leak.answer for leak in view.answer_leaks] == [GOLD_TRAIN]


async def test_validation_answers_are_not_searched(session):
    """The optimizer is never shown them, so a match is a coincidence.

    Only training items reach an analyst — that is what "held out" means. A
    validation gold answer appearing verbatim in the skill cannot have been
    copied from the prompt, so flagging it would report a coincidence with the
    same red banner used for a real leak, and the two would stop being
    distinguishable.
    """
    run = await make_run(session)
    both = {
        "billing/SKILL.md": INITIAL["billing/SKILL.md"] + f"{GOLD_VAL}\n{GOLD_TRAIN}\n"
    }
    await add_step(session, run, both)

    view = await diff(session, run)
    assert [leak.answer for leak in view.answer_leaks] == [GOLD_TRAIN]


async def test_a_question_in_both_splits_is_still_searched(session):
    """Overlap is allowed, and it is the case where a leak does the most damage.

    An item in both splits was shown to an analyst *and* is scored by the gate,
    so memorising it raises the validation score too and the gate accepts the
    step. Deriving the search list from "training only" must mean the row's
    split, not "questions absent from validation".
    """
    run = await make_run(session, golds=(("train", GOLD_TRAIN), ("val", GOLD_TRAIN)))
    leaked = {"billing/SKILL.md": INITIAL["billing/SKILL.md"] + f"{GOLD_TRAIN}\n"}
    await add_step(session, run, leaked)

    view = await diff(session, run)
    assert [leak.answer for leak in view.answer_leaks] == [GOLD_TRAIN]


async def test_a_very_short_gold_answer_does_not_trigger_the_warning(session):
    """"Yes" appears in a well-written skill by accident, and often.

    `skillio.MIN_LEAK_CHARS` exists for this, but the endpoint is where the
    answers are chosen — a filter applied in the wrong place would let every
    boolean question in an eval set produce a red banner on every step.
    """
    run = await make_run(session, golds=(("train", "Yes"),))
    assert len("Yes") < skillio.MIN_LEAK_CHARS
    added = {"billing/SKILL.md": INITIAL["billing/SKILL.md"] + "Yes, refunds are possible.\n"}
    await add_step(session, run, added)

    view = await diff(session, run)
    assert view.answer_leaks == []
