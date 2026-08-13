"""Part 2: what a step did to the skill, against the right baseline.

The load-bearing decision here is which snapshot the diff is taken *against*.
`parent_step_no` is the last step whose candidate the gate **accepted**, which is
usually not `step_no - 1`: a rejected step rolls the skill back, so step 4's
parent may well be step 2. Diffing against `n - 1` would show a rejected step's
edits folded into the next step's diff — attributing one model's proposal to
another, on the page whose entire job is to say who changed what.

The second thing worth protecting is that the line counts on this page come from
`skillio`, the same module the step row and the file tree read. Two independent
answers to "how many lines changed" eventually disagree on screen about one edit.

Needs a real database: the base resolution is a query against sibling rows, and
what is being protected is which row it lands on.
"""
from __future__ import annotations

import os
import uuid

import pytest
from fastapi import HTTPException
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
    not TEST_DB, reason="set TEST_DATABASE_URL to run the database-backed diff tests"
)

INITIAL = {
    "billing/SKILL.md": "# Billing\n\nAnswer refund questions.\n",
    "billing/references/refunds.md": "Refunds take 5 days.\n",
}


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


async def make_run(session, *, subject="alice", best_step=0):
    """A run with the initial skill snapshotted as step 0, and one gold answer."""
    eval_set = EvalSet(name="set", source_format="jsonl", meta={})
    session.add(eval_set)
    await session.flush()
    session.add(EvalSetRole(eval_set_id=eval_set.id, user_subject=subject, role="owner"))

    run = OptimizationRun(
        name="tune billing", created_by=subject, status="completed",
        mode="isolated", skill_name="billing",
        config={"optimizer_model": "gpt-5"},
        secrets={"optimizer_api_key": "sk-live-do-not-ship"},
        initial_skill=dict(INITIAL), detector={},
        num_epochs=1, batch_size=4, steps_per_epoch=4, total_steps=4,
        best_step=best_step, best_score=0.5,
    )
    session.add(run)
    await session.flush()

    session.add(OptimizationItem(
        run_id=run.id, split="train", item_key=f"{eval_set.id}:q0", question_pk=None,
        source_eval_set_id=eval_set.id, question="how long is a refund?",
        ground_truth_response="Refunds settle in exactly 4 business days.",
        ground_truth_reasoning="because", ordinal=0,
    ))
    session.add(OptimizationSkill(
        run_id=run.id, step_no=0, kind="initial", files=dict(INITIAL),
        content_hash="hash-0", per_file_stats={},
    ))
    await session.commit()
    return run


async def add_step(session, run, step_no, files, *, parent_step_no, gate_action="accept",
                   **extra):
    """One step plus the candidate snapshot it produced."""
    before = files if step_no == 0 else None
    step = OptimizationStep(
        run_id=run.id, step_no=step_no, epoch_no=1, step_in_epoch=step_no,
        status="done", parent_step_no=parent_step_no, gate_action=gate_action,
        gate_reject_reason=extra.pop("gate_reject_reason", None),
        edit_summary=extra.pop("edit_summary", "tightened the refund rule"),
        n_edits_applied=extra.pop("n_edits_applied", 1),
        n_edits_skipped=extra.pop("n_edits_skipped", 0),
        edit_reports=extra.pop("edit_reports", []),
        **extra,
    )
    session.add(step)
    session.add(OptimizationSkill(
        run_id=run.id, step_no=step_no, kind="candidate", files=dict(files),
        content_hash=f"hash-{step_no}",
        per_file_stats=skillio.per_file_stats(INITIAL, files),
    ))
    await session.commit()
    return step


def changed(view):
    return {f.path: f for f in view.files}


# --- Which snapshot the diff is taken against --------------------------------


async def test_the_base_is_the_last_accepted_step_not_the_previous_one(session):
    """A rejected step rolls the skill back, so `parent_step_no` skips over it.

    Step 2 is rejected and its edits never reach the skill; step 3 is therefore
    derived from step 1. A diff taken against `step_no - 1` would subtract step
    2's discarded text from step 3's, showing step 3 *deleting* lines it never
    saw and attributing step 2's proposal to it.
    """
    run = await make_run(session)
    one = {**INITIAL, "billing/SKILL.md": "# Billing\n\nAnswer refund questions.\nRefunds are 4 days.\n"}
    two = {**one, "billing/SKILL.md": one["billing/SKILL.md"] + "Escalate anything over $500.\n"}
    three = {**one, "billing/SKILL.md": one["billing/SKILL.md"] + "Ask for the order id.\n"}

    await add_step(session, run, 1, one, parent_step_no=None, gate_action="accept_new_best")
    await add_step(session, run, 2, two, parent_step_no=1, gate_action="reject",
                   gate_reject_reason="accuracy")
    await add_step(session, run, 3, three, parent_step_no=1, gate_action="accept")

    view = await opt.get_step_skill_diff(
        run.id, 3, base="parent", subject="alice", session=session
    )
    assert view.base_step_no == 1
    entry = changed(view)["billing/SKILL.md"]
    assert entry.before == one["billing/SKILL.md"]
    assert "Escalate anything over $500." not in (entry.before or "")
    assert entry.added == 1 and entry.removed == 0


async def test_a_step_with_no_accepted_parent_falls_back_to_the_initial_skill(session):
    """`parent_step_no` is NULL until the gate accepts something.

    Every step before the first acceptance is derived from the skill as it
    arrived. Treating NULL as "no base" would leave the first steps of a run —
    the ones most worth reading — with an empty diff, and treating it as step 0
    silently would hide that the run has not accepted anything yet.
    """
    run = await make_run(session)
    one = {**INITIAL, "billing/SKILL.md": INITIAL["billing/SKILL.md"] + "Refunds are 4 days.\n"}
    await add_step(session, run, 1, one, parent_step_no=None, gate_action="reject",
                   gate_reject_reason="accuracy")

    view = await opt.get_step_skill_diff(
        run.id, 1, base="parent", subject="alice", session=session
    )
    assert view.base_step_no == 0
    assert view.base_is_fallback is True
    assert changed(view)["billing/SKILL.md"].added == 1


async def test_asking_for_the_initial_base_ignores_the_parent(session):
    """The second toggle on the page: everything this run has done so far.

    With a parent of step 2, `base=initial` must still reach back to step 0 —
    that comparison is how a reader judges whether a run drifted somewhere it
    should not have, one accepted step at a time being individually reasonable.
    """
    run = await make_run(session)
    one = {**INITIAL, "billing/SKILL.md": INITIAL["billing/SKILL.md"] + "Refunds are 4 days.\n"}
    two = {**one, "billing/SKILL.md": one["billing/SKILL.md"] + "Ask for the order id.\n"}
    await add_step(session, run, 1, one, parent_step_no=None)
    await add_step(session, run, 2, two, parent_step_no=1)

    view = await opt.get_step_skill_diff(
        run.id, 2, base="initial", subject="alice", session=session
    )
    assert view.base_step_no == 0
    assert view.base_is_fallback is False
    assert changed(view)["billing/SKILL.md"].added == 2


async def test_a_base_that_is_neither_parent_nor_initial_is_refused(session):
    """`base` arrives from a query string, so it can hold anything at all."""
    run = await make_run(session)
    one = {**INITIAL, "billing/SKILL.md": INITIAL["billing/SKILL.md"] + "x\n"}
    await add_step(session, run, 1, one, parent_step_no=None)

    with pytest.raises(HTTPException) as excinfo:
        await opt.get_step_skill_diff(
            run.id, 1, base="best", subject="alice", session=session
        )
    assert excinfo.value.status_code == 400


# --- What the payload has to carry -------------------------------------------


async def test_only_changed_files_carry_their_text_and_the_rest_are_named(session):
    """The tree lists the whole skill; the diff pane only needs what moved.

    Shipping every file's full text on every request grows the payload with the
    skill rather than with the edit. Dropping the untouched files entirely would
    be worse in the other direction — the file tree would shrink to the edited
    files and stop being a picture of the skill.
    """
    run = await make_run(session)
    one = {**INITIAL, "billing/SKILL.md": INITIAL["billing/SKILL.md"] + "Refunds are 4 days.\n"}
    await add_step(session, run, 1, one, parent_step_no=None)

    view = await opt.get_step_skill_diff(
        run.id, 1, base="parent", subject="alice", session=session
    )
    assert [f.path for f in view.files] == ["billing/SKILL.md"]
    assert view.unchanged_paths == ["billing/references/refunds.md"]


async def test_a_file_this_step_created_has_no_left_hand_side(session):
    """A new file and an emptied one must not look the same.

    `append` to a path that does not exist creates a file — that is a supported
    edit and the most common way a run grows a reference document. If a missing
    base were rendered as an empty string, the tree could not label it "new" and
    the diff would show a file that was there all along and had every line added.
    """
    run = await make_run(session)
    one = {**INITIAL, "billing/references/escalation.md": "Escalate over $500.\n"}
    await add_step(session, run, 1, one, parent_step_no=None)

    view = await opt.get_step_skill_diff(
        run.id, 1, base="parent", subject="alice", session=session
    )
    entry = changed(view)["billing/references/escalation.md"]
    assert entry.before is None
    assert entry.after == "Escalate over $500.\n"
    assert entry.added == 1 and entry.removed == 0


async def test_a_file_this_step_emptied_keeps_its_left_hand_side(session):
    """The mirror of the case above, and the one that loses information silently.

    A deleted file whose `after` were rendered as `""` would show as a file that
    still exists and happens to be empty — the diff a reader would use to decide
    whether the agent still has that reference to read.
    """
    run = await make_run(session)
    one = {"billing/SKILL.md": INITIAL["billing/SKILL.md"]}
    await add_step(session, run, 1, one, parent_step_no=None)

    view = await opt.get_step_skill_diff(
        run.id, 1, base="parent", subject="alice", session=session
    )
    entry = changed(view)["billing/references/refunds.md"]
    assert entry.before == "Refunds take 5 days.\n"
    assert entry.after is None
    assert entry.removed == 1 and entry.added == 0


async def test_the_totals_are_the_sum_of_the_per_file_counts_this_page_shows(session):
    """One implementation of "how many lines changed", not two.

    `skillio` computes the counts the step row, the chart tooltip and this tree
    all display. If the endpoint totalled them separately — or the browser
    recounted from the rows it rendered — the header and the tree would
    eventually disagree about the same edit, and neither would be checkable.
    """
    run = await make_run(session)
    one = {
        "billing/SKILL.md": "# Billing\n\nAnswer refund questions.\nRefunds are 4 days.\n",
        "billing/references/escalation.md": "Escalate over $500.\n",
    }
    await add_step(session, run, 1, one, parent_step_no=None)

    view = await opt.get_step_skill_diff(
        run.id, 1, base="parent", subject="alice", session=session
    )
    assert (view.lines_added, view.lines_removed) == skillio.total_line_changes(INITIAL, one)
    assert view.lines_added == sum(f.added for f in view.files)
    assert view.lines_removed == sum(f.removed for f in view.files)


async def test_a_rejected_step_still_has_a_diff_to_read(session):
    """Reading what the gate turned down is the point of the page, not an edge case.

    The candidate is snapshotted whether or not it survived. Refusing to serve a
    rejected step's diff would remove the only way to tell "the model's idea was
    bad" from "the model's idea was fine and the rollout was noisy".
    """
    run = await make_run(session)
    one = {**INITIAL, "billing/SKILL.md": INITIAL["billing/SKILL.md"] + "Refunds are 4 days.\n"}
    await add_step(session, run, 1, one, parent_step_no=None, gate_action="reject",
                   gate_reject_reason="accuracy")

    view = await opt.get_step_skill_diff(
        run.id, 1, base="parent", subject="alice", session=session
    )
    assert view.gate_action == "reject"
    assert view.gate_reject_reason == "accuracy"
    assert view.is_best is False
    assert changed(view)["billing/SKILL.md"].added == 1


async def test_the_edits_that_never_reached_the_skill_say_why(session):
    """A count cannot distinguish a bad idea from a typo in a target string.

    "2 edits were skipped" is compatible with the model proposing changes to a
    protected region, naming a path outside the skill, or simply mistyping the
    text it meant to replace — three different problems with three different
    responses. The reason is decided inside `apply_patch_with_report` and cannot
    be recomputed at read time, so it is either persisted or it is lost.
    """
    run = await make_run(session)
    one = {**INITIAL, "billing/SKILL.md": INITIAL["billing/SKILL.md"] + "Refunds are 4 days.\n"}
    reports = [
        {"index": 1, "op": "append", "path": "billing/SKILL.md", "path_defaulted": False,
         "target": "", "content_preview": "Refunds are 4 days.", "status": "applied_append"},
        {"index": 2, "op": "replace", "path": "billing/SKILL.md", "path_defaulted": False,
         "target": "Refunds take 6 days", "content_preview": "Refunds take 4 days",
         "status": "skipped_replace_target_not_found"},
    ]
    await add_step(session, run, 1, one, parent_step_no=None, n_edits_applied=1,
                   n_edits_skipped=1, edit_reports=reports)

    view = await opt.get_step_skill_diff(
        run.id, 1, base="parent", subject="alice", session=session
    )
    assert [r.status for r in view.edit_reports] == [
        "applied_append", "skipped_replace_target_not_found",
    ]
    assert view.edit_reports[1].target == "Refunds take 6 days"
    # Both counts, because the page states them as a pair ("1 of 2 applied") and
    # a missing numerator reads as "none of them landed".
    assert (view.n_edits_applied, view.n_edits_skipped) == (1, 1)


async def test_the_best_step_says_so(session):
    """The banner's wording turns on it, and the run row is the only place it lives."""
    run = await make_run(session, best_step=1)
    one = {**INITIAL, "billing/SKILL.md": INITIAL["billing/SKILL.md"] + "Refunds are 4 days.\n"}
    await add_step(session, run, 1, one, parent_step_no=None, gate_action="accept_new_best")

    view = await opt.get_step_skill_diff(
        run.id, 1, base="parent", subject="alice", session=session
    )
    assert view.is_best is True
    assert view.gate_action == "accept_new_best"


async def test_the_diff_never_carries_the_run_credentials(session):
    """Same rule as every other response: `secrets` is a column nothing reads."""
    run = await make_run(session)
    one = {**INITIAL, "billing/SKILL.md": INITIAL["billing/SKILL.md"] + "Refunds are 4 days.\n"}
    await add_step(session, run, 1, one, parent_step_no=None)

    view = await opt.get_step_skill_diff(
        run.id, 1, base="parent", subject="alice", session=session
    )
    assert "sk-live-do-not-ship" not in view.model_dump_json()


# --- Refusals ----------------------------------------------------------------


async def test_a_run_the_caller_cannot_read_has_no_diff(session):
    """The skill text is the run's own work product, and the payload quotes it whole."""
    run = await make_run(session, subject="alice")
    one = {**INITIAL, "billing/SKILL.md": INITIAL["billing/SKILL.md"] + "Refunds are 4 days.\n"}
    await add_step(session, run, 1, one, parent_step_no=None)

    with pytest.raises(HTTPException) as excinfo:
        await opt.get_step_skill_diff(
            run.id, 1, base="parent", subject="mallory", session=session
        )
    assert excinfo.value.status_code == 404


async def test_an_unknown_step_is_a_404(session):
    run = await make_run(session)
    with pytest.raises(HTTPException) as excinfo:
        await opt.get_step_skill_diff(
            run.id, 9, base="parent", subject="alice", session=session
        )
    assert excinfo.value.status_code == 404


async def test_a_step_that_was_interrupted_before_it_produced_a_candidate_is_a_404(session):
    """An aborted step has a row and no snapshot, and that is a reachable state.

    The step row is written when the step starts; the candidate only exists once
    the update stage finishes. Serving an empty diff for the gap between them
    would read as "this step changed nothing", which is a claim about the skill
    rather than about the run being cut short.
    """
    run = await make_run(session)
    session.add(OptimizationStep(
        run_id=run.id, step_no=1, epoch_no=1, step_in_epoch=1, status="aborted",
        parent_step_no=None, abort_reason="backend restarted",
    ))
    await session.commit()

    with pytest.raises(HTTPException) as excinfo:
        await opt.get_step_skill_diff(
            run.id, 1, base="parent", subject="alice", session=session
        )
    assert excinfo.value.status_code == 404


# --- When an epoch boundary wrote into the skill -----------------------------


async def add_slow_update(session, run, step_no, files):
    """The skill as an epoch boundary left it, recorded against the step it followed."""
    session.add(OptimizationSkill(
        run_id=run.id, step_no=step_no, kind="slow_update", files=dict(files),
        content_hash=f"slow-{step_no}", per_file_stats={},
    ))
    await session.commit()


async def test_the_base_is_the_skill_the_step_actually_started_from(session):
    """A step's diff must not include a block written by the epoch boundary.

    The slow update edits the skill *between* steps: it runs after the last step
    of an epoch and writes guidance into a protected block. The next step is
    derived from that version, so diffing it against the parent's *candidate*
    would show a paragraph the next step's analyst never wrote, attributed to it
    — the same misattribution `parent_step_no` exists to prevent, arriving by a
    different route.
    """
    run = await make_run(session)
    one = {**INITIAL, "billing/SKILL.md": INITIAL["billing/SKILL.md"] + "Refunds are 4 days.\n"}
    after_boundary = {
        **one,
        "billing/SKILL.md": one["billing/SKILL.md"] + "<!-- SLOW_UPDATE_START -->\nguidance\n<!-- SLOW_UPDATE_END -->\n",
    }
    two = {**after_boundary,
           "billing/SKILL.md": after_boundary["billing/SKILL.md"] + "Ask for the order id.\n"}

    await add_step(session, run, 1, one, parent_step_no=None, gate_action="accept_new_best")
    await add_slow_update(session, run, 1, after_boundary)
    await add_step(session, run, 2, two, parent_step_no=1, gate_action="accept")

    view = await opt.get_step_skill_diff(
        run.id, 2, base="parent", subject="alice", session=session
    )
    entry = changed(view)["billing/SKILL.md"]
    assert "guidance" in (entry.before or "")
    assert entry.added == 1 and entry.removed == 0


async def test_a_step_is_still_shown_by_its_own_candidate_not_the_boundary_that_followed(session):
    """The other side of the same rule.

    Step 1's page is about step 1's edits. Reading the slow-update snapshot as
    *its* result would credit the analyst with a block written afterwards by a
    different pass, on a different prompt, about a different question.
    """
    run = await make_run(session)
    one = {**INITIAL, "billing/SKILL.md": INITIAL["billing/SKILL.md"] + "Refunds are 4 days.\n"}
    after_boundary = {
        **one,
        "billing/SKILL.md": one["billing/SKILL.md"] + "<!-- SLOW_UPDATE_START -->\nguidance\n<!-- SLOW_UPDATE_END -->\n",
    }
    await add_step(session, run, 1, one, parent_step_no=None, gate_action="accept_new_best")
    await add_slow_update(session, run, 1, after_boundary)

    view = await opt.get_step_skill_diff(
        run.id, 1, base="parent", subject="alice", session=session
    )
    entry = changed(view)["billing/SKILL.md"]
    assert "guidance" not in (entry.after or "")
    assert entry.added == 1
