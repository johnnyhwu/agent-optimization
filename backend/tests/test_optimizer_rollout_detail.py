"""Part 1: one step, one split, and the analyst calls it fed.

This is the page that answers "why did the optimizer propose *that*?", and the
answer is only checkable if three things line up: the questions as this run
snapshotted them, the rollout that measured them, and the minibatches the
failures were grouped into before an analyst ever saw them.

The load-bearing part is the grouping. `optimization_results.minibatch_no` is
the only link between a question and the analyst call it was evidence for, and
it is written *after* the rollout row — the split into minibatches does not
exist until the reflect stage runs. Nothing else in the system reconstructs it,
so a page that groups by anything else is showing a plausible fiction.

Needs a real database: the linkage is one UPDATE with a subquery, and the thing
worth protecting is which rows it reaches.
"""
from __future__ import annotations

import os
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models import (
    EvalSet,
    EvalSetRole,
    OptimizationItem,
    OptimizationResult,
    OptimizationRun,
    OptimizationStep,
    Question,
)
from app.optimizer.store import DbOptimizationStore, ResultRow, RolloutSummary
from app.routers import optimization as opt

TEST_DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="set TEST_DATABASE_URL to run the database-backed detail tests"
)


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


def result_row(key, *, verdict="incorrect", score=0.0, status="done", **extra):
    return ResultRow(
        item_key=key,
        question_pk=extra.pop("question_pk", None),
        correlation_id=f"corr-{key}",
        agent_response=extra.pop("agent_response", f"answer for {key}"),
        agent_latency_ms=extra.pop("agent_latency_ms", 1200),
        verdict=verdict,
        judge_score=score,
        judge_comment=extra.pop("judge_comment", "missed the rule"),
        status=status,
        failure_kind=extra.pop("failure_kind", None),
        error_message=extra.pop("error_message", None),
        activated=extra.pop("activated", True),
        skills_read=extra.pop("skills_read", ["billing"]),
        detector_hit=extra.pop("detector_hit", "tool_path"),
        trace_ready=extra.pop("trace_ready", True),
        **extra,
    )


def summary(split, rows, **extra):
    return RolloutSummary(
        split=split,
        skill_step_no=extra.pop("skill_step_no", 0),
        n_items=len(rows),
        n_scored=extra.pop("n_scored", len(rows)),
        n_agent_error=extra.pop("n_agent_error", 0),
        n_judge_error=extra.pop("n_judge_error", 0),
        hard=extra.pop("hard", 0.5),
        soft=extra.pop("soft", 0.6),
        activation_rate=extra.pop("activation_rate", 1.0),
        n_activated=extra.pop("n_activated", len(rows)),
        latency_min_ms=900, latency_p50_ms=1200, latency_max_ms=4000,
        results=rows,
        **extra,
    )


async def make_run(session, *, subject="alice", overlap=False):
    """A run with one real eval set, a snapshot, and a step 1 ready for rollouts."""
    eval_set = EvalSet(name="set", source_format="jsonl", meta={})
    session.add(eval_set)
    await session.flush()
    session.add(EvalSetRole(eval_set_id=eval_set.id, user_subject=subject, role="owner"))

    questions = {}
    for i in range(6):
        q = Question(
            eval_set_id=eval_set.id, question_id=f"q{i}", question=f"live text of q{i}",
            ground_truth_response=f"live gold {i}", ground_truth_reasoning="because",
        )
        session.add(q)
        await session.flush()
        questions[f"q{i}"] = q

    run = OptimizationRun(
        name="tune billing", created_by=subject, status="completed",
        mode="isolated", skill_name="billing",
        config={"optimizer_model": "gpt-5"},
        secrets={"optimizer_api_key": "sk-live-do-not-ship"},
        initial_skill={"billing/SKILL.md": "x"}, detector={},
        num_epochs=1, batch_size=4, steps_per_epoch=1, total_steps=1,
        best_step=0, best_score=0.5,
    )
    session.add(run)
    await session.flush()

    keys = {}
    for i in range(6):
        split = "train" if i < 4 else "val"
        key = f"{eval_set.id}:q{i}"
        keys[f"q{i}"] = key
        session.add(OptimizationItem(
            run_id=run.id, split=split, item_key=key,
            question_pk=questions[f"q{i}"].id, source_eval_set_id=eval_set.id,
            # Snapshot text, deliberately different from the live question above.
            question=f"snapshot text of q{i}",
            ground_truth_response=f"snapshot gold {i}",
            ground_truth_reasoning="because", ordinal=i,
        ))
    if overlap:
        session.add(OptimizationItem(
            run_id=run.id, split="val", item_key=keys["q0"],
            question_pk=questions["q0"].id, source_eval_set_id=eval_set.id,
            question="snapshot text of q0", ground_truth_response="snapshot gold 0",
            ground_truth_reasoning="because", ordinal=99,
        ))

    step = OptimizationStep(
        run_id=run.id, step_no=1, epoch_no=1, step_in_epoch=1, status="done",
        gate_action="reject", gate_reject_reason="accuracy", parent_step_no=0,
        edit_summary="tightened the refund rule",
    )
    session.add(step)
    await session.commit()
    return run, step, keys


# --- The link between a question and the analyst call it fed -----------------


async def test_recording_a_minibatch_marks_the_questions_it_consumed(session):
    """`minibatch_no` on the result rows is written by the reflect stage.

    The rollout row is persisted before the minibatch split exists — the split
    is decided inside the update stage, from the results. If nothing writes the
    number back afterwards the column stays NULL for every run, and Part 1 has
    no way to show which failures were shown to the analyst together, which is
    the one thing the page exists to explain.
    """
    run, step, keys = await make_run(session)
    store = DbOptimizationStore(session)
    await store.record_rollout(
        step.id, summary("train", [result_row(keys[f"q{i}"]) for i in range(4)])
    )

    await store.record_minibatch(
        step.id, minibatch_no=0, source_type="failure", n_items=2,
        item_keys=[keys["q0"], keys["q1"]], prompt_system="s", prompt_user="u",
        raw_output={}, truncation=[], chars_before=10, chars_after=10,
        error=None, duration_ms=5,
    )
    await store.record_minibatch(
        step.id, minibatch_no=1, source_type="failure", n_items=2,
        item_keys=[keys["q2"], keys["q3"]], prompt_system="s", prompt_user="u",
        raw_output={}, truncation=[], chars_before=10, chars_after=10,
        error=None, duration_ms=5,
    )

    rows = (await session.scalars(select(OptimizationResult))).all()
    assert {r.item_key: r.minibatch_no for r in rows} == {
        keys["q0"]: 0, keys["q1"]: 0, keys["q2"]: 1, keys["q3"]: 1,
    }


async def test_a_question_in_both_splits_is_only_numbered_on_the_training_row(session):
    """Validation is never reflected on, even when it holds the same question.

    Overlap is a supported choice, so the same `item_key` can have a row in both
    rollouts. An update that matched on `item_key` alone would stamp a minibatch
    number onto the validation row too — and Part 1 would then show a validation
    question as evidence for an edit, which is exactly the confusion the split
    exists to prevent.
    """
    run, step, keys = await make_run(session, overlap=True)
    store = DbOptimizationStore(session)
    await store.record_rollout(step.id, summary("train", [result_row(keys["q0"])]))
    await store.record_rollout(step.id, summary("val", [result_row(keys["q0"])]))

    await store.record_minibatch(
        step.id, minibatch_no=0, source_type="failure", n_items=1,
        item_keys=[keys["q0"]], prompt_system="s", prompt_user="u", raw_output={},
        truncation=[], chars_before=1, chars_after=1, error=None, duration_ms=1,
    )

    rows = {
        r.split: r.minibatch_no
        for r in (
            await session.execute(
                select(opt.OptimizationRollout.split, OptimizationResult.minibatch_no)
                .join(
                    OptimizationResult,
                    OptimizationResult.rollout_id == opt.OptimizationRollout.id,
                )
            )
        ).all()
    }
    assert rows == {"train": 0, "val": None}


async def test_numbering_one_step_does_not_reach_another(session):
    """Two steps of one run hold the same `item_key`s, batch after batch.

    An update scoped to the run rather than to the step would renumber every
    earlier step's rows on every step — so the whole run would end up showing
    the last step's grouping, and no earlier step's page would be true.
    """
    run, step, keys = await make_run(session)
    later = OptimizationStep(
        run_id=run.id, step_no=2, epoch_no=1, step_in_epoch=2, status="done",
    )
    session.add(later)
    await session.commit()

    store = DbOptimizationStore(session)
    await store.record_rollout(step.id, summary("train", [result_row(keys["q0"])]))
    await store.record_rollout(later.id, summary("train", [result_row(keys["q0"])]))

    await store.record_minibatch(
        later.id, minibatch_no=3, source_type="failure", n_items=1,
        item_keys=[keys["q0"]], prompt_system="s", prompt_user="u", raw_output={},
        truncation=[], chars_before=1, chars_after=1, error=None, duration_ms=1,
    )

    numbers = {}
    for rollout_id, number in (
        await session.execute(
            select(OptimizationResult.rollout_id, OptimizationResult.minibatch_no)
        )
    ).all():
        numbers[rollout_id] = number
    assert sorted(numbers.values(), key=lambda v: (v is None, v)) == [3, None]


# --- The page's payload ------------------------------------------------------


async def test_the_detail_carries_the_rollout_header_and_every_question(session):
    """One request for the whole page: the numbers, the questions, the analysts.

    Three round trips would each need their own loading state on a page whose
    parts are meaningless apart — an accuracy without its questions, a minibatch
    without the failures it was built from.
    """
    run, step, keys = await make_run(session)
    store = DbOptimizationStore(session)
    await store.record_rollout(
        step.id, summary("train", [result_row(keys[f"q{i}"]) for i in range(4)])
    )

    detail = await opt.get_rollout_detail(
        run.id, 1, "train", subject="alice", session=session
    )
    assert detail.hard == 0.5
    assert detail.n_items == 4
    assert detail.latency_p50_ms == 1200
    assert detail.gate_action == "reject"
    assert detail.edit_summary == "tightened the refund rule"
    assert [r.item_key for r in detail.results] == [keys[f"q{i}"] for i in range(4)]


async def test_the_questions_shown_are_the_ones_the_run_snapshotted(session):
    """A question edited after the run must not change what the page says it asked.

    The whole point of snapshotting the items is that a run stays readable when
    its source eval set moves on. Reading `questions` here instead of
    `optimization_items` would put today's text beside a six-week-old answer and
    a judge verdict about a different question.
    """
    run, step, keys = await make_run(session)
    store = DbOptimizationStore(session)
    await store.record_rollout(step.id, summary("train", [result_row(keys["q0"])]))

    detail = await opt.get_rollout_detail(
        run.id, 1, "train", subject="alice", session=session
    )
    assert detail.results[0].question == "snapshot text of q0"
    assert detail.results[0].ground_truth_response == "snapshot gold 0"


async def test_failed_questions_are_listed_rather_than_dropped(session):
    """An agent error is excluded from the *scores*, not from the page.

    `score_rollout` leaves failures out of the numerator and the denominator,
    which is right. Leaving them out of the list too would make a rollout of 21
    questions show 20 rows and no explanation — and the missing one is the row a
    developer most needs to see.
    """
    run, step, keys = await make_run(session)
    store = DbOptimizationStore(session)
    rows = [
        result_row(keys["q0"]),
        result_row(keys["q1"], status="failed", verdict=None, score=None,
                   failure_kind="agent_timeout", error_message="timed out after 60s"),
    ]
    await store.record_rollout(step.id, summary("train", rows, n_scored=1, n_agent_error=1))

    detail = await opt.get_rollout_detail(
        run.id, 1, "train", subject="alice", session=session
    )
    assert len(detail.results) == 2
    failed = next(r for r in detail.results if r.status == "failed")
    assert failed.failure_kind == "agent_timeout"
    assert failed.error_message == "timed out after 60s"
    assert detail.n_agent_error == 1


async def test_the_minibatches_carry_the_prompt_that_was_actually_sent(session):
    """Not a reconstruction of it. The prompt is the evidence.

    "Why did it propose that?" is answerable only against the text the model
    received — after truncation, which is the step most likely to be the
    explanation. Rebuilding the prompt at read time from the stored items would
    produce something that looks right and differs in exactly the way that
    mattered.
    """
    run, step, keys = await make_run(session)
    store = DbOptimizationStore(session)
    await store.record_rollout(
        step.id, summary("train", [result_row(keys[f"q{i}"]) for i in range(2)])
    )
    await store.record_minibatch(
        step.id, minibatch_no=0, source_type="failure", n_items=2,
        item_keys=[keys["q0"], keys["q1"]],
        prompt_system="You are an analyst.", prompt_user="## Current Skill\n…",
        raw_output={"failure_summary": ["rule_missing"], "patch": {"edits": [1, 2]}},
        truncation=[{"item_key": keys["q0"], "span_index": 3, "field": "obs",
                     "before": 40000, "after": 6000, "stage": "tool_result"}],
        chars_before=41200, chars_after=12000, error=None, duration_ms=8100,
    )

    detail = await opt.get_rollout_detail(
        run.id, 1, "train", subject="alice", session=session
    )
    batch = detail.minibatches[0]
    assert batch.prompt_system == "You are an analyst."
    assert batch.prompt_user.startswith("## Current Skill")
    assert batch.raw_output["failure_summary"] == ["rule_missing"]
    assert batch.chars_before == 41200 and batch.chars_after == 12000
    assert batch.truncation[0]["span_index"] == 3


async def test_the_stages_after_the_analysts_are_returned_in_pipeline_order(session):
    """Merge and ranking are where a proposed edit usually stops being one.

    They were not stored at all, so the page went from "the analyst asked for
    this" straight to "the skill says that" with the deciding calls invisible.
    """
    run, step, keys = await make_run(session)
    store = DbOptimizationStore(session)
    await store.record_rollout(step.id, summary("train", [result_row(keys["q0"])]))
    for seq, stage in enumerate(["merge_failure", "merge_final", "ranking"]):
        await store.record_stage_call(
            step.id, seq=seq, stage=stage, level=1 if seq == 0 else None,
            prompt_system=f"{stage} system", prompt_user=f"{stage} user",
            output={"reasoning": stage}, error=None, duration_ms=1200,
        )

    detail = await opt.get_rollout_detail(
        run.id, 1, "train", subject="alice", session=session
    )

    assert [c.stage for c in detail.stage_calls] == [
        "merge_failure", "merge_final", "ranking",
    ]
    assert detail.stage_calls[0].prompt_user == "merge_failure user"
    assert detail.stage_calls[0].level == 1
    assert detail.stage_calls[-1].output == {"reasoning": "ranking"}


async def test_the_validation_split_shows_no_stages(session):
    """Same rule as the minibatches: merge and ranking produced the candidate,
    and showing them beside the held-out questions implies the gate saw them."""
    run, step, keys = await make_run(session)
    store = DbOptimizationStore(session)
    await store.record_rollout(step.id, summary("train", [result_row(keys["q0"])]))
    await store.record_rollout(step.id, summary("val", [result_row(keys["q4"])]))
    await store.record_stage_call(
        step.id, seq=0, stage="merge_final", level=None,
        prompt_system="s", prompt_user="u", output={}, error=None, duration_ms=1,
    )

    detail = await opt.get_rollout_detail(
        run.id, 1, "val", subject="alice", session=session
    )

    assert detail.stage_calls == []


async def test_a_step_recorded_before_stages_were_stored_reports_none(session):
    """Older runs have no rows here and cannot be backfilled — the prompts were
    never written down. The page has to be able to say so."""
    run, step, keys = await make_run(session)
    store = DbOptimizationStore(session)
    await store.record_rollout(step.id, summary("train", [result_row(keys["q0"])]))

    detail = await opt.get_rollout_detail(
        run.id, 1, "train", subject="alice", session=session
    )

    assert detail.stage_calls == []


async def test_a_minibatch_whose_analyst_failed_says_so(session):
    """One failed analyst call does not end the step, and must not vanish either.

    The step continues with the patches it did get. If the failure were dropped
    from the page, a step that reflected on half its failures would look
    identical to one that reflected on all of them.
    """
    run, step, keys = await make_run(session)
    store = DbOptimizationStore(session)
    await store.record_rollout(step.id, summary("train", [result_row(keys["q0"])]))
    await store.record_minibatch(
        step.id, minibatch_no=0, source_type="failure", n_items=1,
        item_keys=[keys["q0"]], prompt_system="s", prompt_user="u", raw_output=None,
        truncation=[], chars_before=1, chars_after=1,
        error="APITimeoutError: request timed out", duration_ms=60000,
    )

    detail = await opt.get_rollout_detail(
        run.id, 1, "train", subject="alice", session=session
    )
    assert detail.minibatches[0].error == "APITimeoutError: request timed out"
    assert detail.minibatches[0].raw_output is None


async def test_the_validation_split_has_no_minibatches(session):
    """Validation is measured, never reflected on.

    If the endpoint returned the step's minibatches regardless of split, the
    validation page would show the analyst calls built from *training* failures
    beside held-out questions — implying the two were connected, which is the
    one thing the gate depends on not being true.
    """
    run, step, keys = await make_run(session)
    store = DbOptimizationStore(session)
    await store.record_rollout(step.id, summary("train", [result_row(keys["q0"])]))
    await store.record_rollout(step.id, summary("val", [result_row(keys["q4"])]))
    await store.record_minibatch(
        step.id, minibatch_no=0, source_type="failure", n_items=1,
        item_keys=[keys["q0"]], prompt_system="s", prompt_user="u", raw_output={},
        truncation=[], chars_before=1, chars_after=1, error=None, duration_ms=1,
    )

    detail = await opt.get_rollout_detail(
        run.id, 1, "val", subject="alice", session=session
    )
    assert detail.minibatches == []
    assert detail.results[0].minibatch_no is None


async def test_the_detail_never_carries_the_run_credentials(session):
    """Same rule as every other response: `secrets` is a column nothing reads."""
    run, step, keys = await make_run(session)
    store = DbOptimizationStore(session)
    await store.record_rollout(step.id, summary("train", [result_row(keys["q0"])]))

    detail = await opt.get_rollout_detail(
        run.id, 1, "train", subject="alice", session=session
    )
    assert "sk-live-do-not-ship" not in detail.model_dump_json()


# --- Refusals ----------------------------------------------------------------


async def test_a_run_the_caller_cannot_read_has_no_rollout_detail(session):
    """This payload is question text and gold answers — the protected thing itself."""
    run, step, keys = await make_run(session, subject="alice")
    store = DbOptimizationStore(session)
    await store.record_rollout(step.id, summary("train", [result_row(keys["q0"])]))

    with pytest.raises(HTTPException) as excinfo:
        await opt.get_rollout_detail(run.id, 1, "train", subject="mallory", session=session)
    assert excinfo.value.status_code == 404


async def test_a_split_that_was_never_rolled_out_is_a_404(session):
    """Step 0 has no training rollout — there is no candidate to train on yet.

    An empty page with zeroed figures would read as "the baseline scored 0 on
    training", which is a much worse answer than "this does not exist".
    """
    run, step, keys = await make_run(session)
    store = DbOptimizationStore(session)
    await store.record_rollout(step.id, summary("val", [result_row(keys["q4"])]))

    with pytest.raises(HTTPException) as excinfo:
        await opt.get_rollout_detail(run.id, 1, "train", subject="alice", session=session)
    assert excinfo.value.status_code == 404


async def test_an_unknown_step_is_a_404(session):
    run, step, keys = await make_run(session)
    with pytest.raises(HTTPException) as excinfo:
        await opt.get_rollout_detail(run.id, 9, "train", subject="alice", session=session)
    assert excinfo.value.status_code == 404


async def test_a_split_that_is_not_train_or_val_is_refused(session):
    """The split is a path segment, so anything at all can arrive in it."""
    run, step, keys = await make_run(session)
    with pytest.raises(HTTPException) as excinfo:
        await opt.get_rollout_detail(run.id, 1, "test", subject="alice", session=session)
    assert excinfo.value.status_code == 400


# --- One question's trace ----------------------------------------------------


async def test_the_trace_view_quotes_the_snapshot_not_the_live_question(session):
    """Same rule as the list, and it matters more here.

    The trace view puts the answer next to what it was graded against. Reading
    the live `questions` row would show the agent being marked wrong against a
    gold answer that did not exist when it answered.
    """
    run, step, keys = await make_run(session)
    store = DbOptimizationStore(session)
    await store.record_rollout(step.id, summary("train", [result_row(keys["q0"])]))
    result = (await session.scalars(select(OptimizationResult))).one()

    view = await opt.get_rollout_result_trace(
        run.id, 1, "train", result.id, subject="alice", session=session
    )
    assert view.ground_truth_response == "snapshot gold 0"
    assert view.agent_response == f"answer for {keys['q0']}"
    assert view.verdict == "incorrect"


async def test_a_result_from_another_run_is_not_reachable_through_this_path(session):
    """The result id is a uuid in a path that already names a run and a step.

    Trusting it alone would make every other segment decorative — and the id of
    a result in a run the caller cannot read is exactly as guessable as any
    other uuid, which is to say it only has to leak once.
    """
    run_a, step_a, keys_a = await make_run(session, subject="alice")
    run_b, step_b, keys_b = await make_run(session, subject="bob")
    store = DbOptimizationStore(session)
    await store.record_rollout(step_a.id, summary("train", [result_row(keys_a["q0"])]))
    await store.record_rollout(step_b.id, summary("train", [result_row(keys_b["q0"])]))

    foreign = (
        await session.scalars(
            select(OptimizationResult)
            .join(
                opt.OptimizationRollout,
                opt.OptimizationRollout.id == OptimizationResult.rollout_id,
            )
            .where(opt.OptimizationRollout.step_id == step_b.id)
        )
    ).one()

    with pytest.raises(HTTPException) as excinfo:
        await opt.get_rollout_result_trace(
            run_a.id, 1, "train", foreign.id, subject="alice", session=session
        )
    assert excinfo.value.status_code == 404


async def test_a_question_that_failed_has_no_trace_to_fetch(session):
    """The agent never answered, so there is no correlation id worth polling.

    Calling the trace store anyway spends the poll budget inside the request and
    reports "still ingesting" for a trace that will never exist.
    """
    run, step, keys = await make_run(session)
    store = DbOptimizationStore(session)
    await store.record_rollout(step.id, summary("train", [
        result_row(keys["q0"], status="failed", verdict=None, score=None,
                   agent_response=None, failure_kind="agent", trace_ready=False,
                   error_message="connection refused"),
    ]))
    result = (await session.scalars(select(OptimizationResult))).one()

    view = await opt.get_rollout_result_trace(
        run.id, 1, "train", result.id, subject="alice", session=session
    )
    assert view.trace_state == "no_trace"
    assert view.spans == []


# --- What the epoch boundary reads back -------------------------------------


async def test_validation_results_are_read_back_in_the_shape_the_slow_update_wants(session):
    """The epoch boundary compares two steps it may not have executed itself.

    A resumed run has to be able to compare across a boundary whose first half
    ran in a process that is gone, so the comparison reads from storage rather
    than from anything the loop kept in memory. `hard` is the field upstream
    branches on to classify a sample as improved, regressed or a persistent
    failure, and it is derived from the verdict — a soft score of 0.9 is not a
    pass, and treating it as one would report improvements that never happened.
    """
    run, step, keys = await make_run(session)
    store = DbOptimizationStore(session)
    await store.record_rollout(step.id, summary("val", [
        result_row(keys["q4"], verdict="correct", score=1.0),
        result_row(keys["q5"], verdict="incorrect", score=0.9),
    ]))

    rows = {r["id"]: r for r in await store.load_val_results(run.id, 1)}
    assert rows[keys["q4"]]["hard"] == 1
    assert rows[keys["q5"]]["hard"] == 0
    assert rows[keys["q5"]]["soft"] == 0.9
    assert rows[keys["q4"]]["predicted_answer"] == f"answer for {keys['q4']}"


async def test_the_training_split_is_not_returned_as_a_comparison_set(session):
    """Training questions are a different draw every step.

    Feeding them to a longitudinal comparison would attribute the difference
    between two *sets of questions* to the difference between two skills, which
    is the one thing the pass exists to measure. The split filter is the whole
    reason the validation set was chosen as the comparison set.
    """
    run, step, keys = await make_run(session)
    store = DbOptimizationStore(session)
    await store.record_rollout(step.id, summary("train", [result_row(keys["q0"])]))
    await store.record_rollout(step.id, summary("val", [result_row(keys["q4"])]))

    assert [r["id"] for r in await store.load_val_results(run.id, 1)] == [keys["q4"]]


async def test_another_step_of_the_same_run_is_not_mixed_in(session):
    """Both sides of the comparison are one step each, and they are adjacent.

    A read scoped to the run rather than the step would hand the boundary every
    validation result the run ever produced, and every sample would appear
    several times with different outcomes.
    """
    run, step, keys = await make_run(session)
    later = OptimizationStep(
        run_id=run.id, step_no=2, epoch_no=1, step_in_epoch=2, status="done",
    )
    session.add(later)
    await session.commit()

    store = DbOptimizationStore(session)
    await store.record_rollout(step.id, summary("val", [result_row(keys["q4"])]))
    await store.record_rollout(later.id, summary("val", [result_row(keys["q5"])]))

    assert [r["id"] for r in await store.load_val_results(run.id, 2)] == [keys["q5"]]
