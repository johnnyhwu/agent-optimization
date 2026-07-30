"""Run orchestrator (§6.15).

Runs as a background asyncio task with its own DB session. All of the run's
`question_results` rows are created up front, so the UI can list every question
from the first second of a run and colour each one as it moves through:

    pending -> answered (agent replied) -> judged (correct/incorrect)

Per question (from a snapshot read at run start):

    agent (correlation_id in request metadata)
      -> judge -> write question_results
      -> poll trace until ready (backoff) -> set trace_ready
      -> if incorrect: fetch+truncate trace -> diagnose -> write span_analyses
      -> push live progress (SSE)

Progress is published at every one of those boundaries, not just at the end:
`question_started`, `question_answered`, `question_judged`, `question_traced`,
`question_done`. The last three matter because the trace poll and the diagnosis
together can run for tens of seconds against real services, and the detail view
refetches the open question's trace whenever one of these events changes its
phase, verdict, trace_ready or has_analysis.

Failure policy, which matters once the seams are real services rather than
fakes that never raise:

  * A question that fails (agent error, judge error, timeout) is marked
    status='failed' with an error_message, and the run continues — partial
    completion, as before.
  * A diagnosis failure never fails the question. The verdict is the result;
    the diagnosis is an extra. The reason is recorded on the question so the UI
    can say why there is no diagnosis.
  * A trace that cannot be fetched does not fail the question either, but the
    reason is recorded: "Langfuse is unreachable" must not look the same as
    "ingestion hasn't landed yet".
  * Any unexpected error still finalizes the run (status='failed') and still
    publishes a terminal SSE event. A run must never be left stuck in
    'running' with a stream nobody will ever close.

Cancellation (§9.14) is cooperative but immediate: the cancel event is raced
against the in-flight agent/judge call rather than checked between questions,
because one real agent question can take tens of seconds — which is precisely
when someone reaches for the stop button.

The four per-question steps themselves, and the retry / timeout / cancel policies
around them, live in `app/pipeline.py` — the playground (§10) runs the same
sequence for one ad-hoc question. What stays here is everything that is about a
*run*: the question snapshot, the `question_results` rows, the done/total
counters, and the progress events.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app import cancellation
from app.config import settings
from app.db import SessionLocal
from app.integrations import Seams, build_seams
from app.models import EvalSet, Question, QuestionResult, Run, SpanAnalysis
from app.pipeline import (
    RunCancelled,
    call_agent,
    call_judge,
    clip,
    run_diagnosis,
    wait_for_trace,
)
from app.services.aggregation import result_phase
from app.sse import hub

log = logging.getLogger(__name__)


async def run_eval(run_id: uuid.UUID) -> None:
    async with SessionLocal() as session:
        run = await session.get(Run, run_id)
        if run is None:
            return

        try:
            await _execute_run(session, run)
        except Exception as exc:  # noqa: BLE001 - last line of defence
            log.exception("run %s failed", run_id)
            await _finalize_failed(session, run_id, exc)
        finally:
            cancellation.clear(run_id)


async def _execute_run(session, run: Run, seams: Seams | None = None) -> None:
    run_id = run.id
    cancel_event = cancellation.event_for(run_id)

    # The endpoints this run was triggered against (§9.2). Blank keys fall back
    # to the environment, so a run started before per-run config existed — or the
    # seeded fake demo — behaves exactly as it used to.
    config = run.config or {}
    seams = seams or build_seams(config, run.secrets or {})
    agent_timeout_s = config.get("agent_timeout_s") or settings.agent_timeout_s

    # Snapshot the question set at run start (§6.15) — later edits don't affect
    # this run.
    questions = (
        await session.scalars(
            select(Question).where(Question.eval_set_id == run.eval_set_id)
        )
    ).all()
    # Eager-load reasoning + text now (snapshot values).
    snapshot = [
        {
            "pk": q.id,
            "question": q.question,
            "reasoning": q.ground_truth_reasoning,
            "ground_truth": q.ground_truth_response,
        }
        for q in questions
    ]

    # Every result row exists before the first agent call, so the detail view can
    # list the whole question set immediately (greyed out) instead of having
    # questions pop into existence one at a time. It also makes the SSE snapshot's
    # `total` correct for a subscriber that joins late.
    for item in snapshot:
        result = QuestionResult(
            run_id=run_id,
            question_pk=item["pk"],
            correlation_id=uuid.uuid4().hex,
            status="pending",
            trace_ready=False,
        )
        session.add(result)
        item["result"] = result
    await session.commit()

    total = len(snapshot)
    await hub.publish(run_id, {"type": "run_started", "total": total})

    # Agent metadata (§6.2): user_id is who triggered this run, tags carries
    # the eval set name — computed once per run, not per question.
    eval_set = await session.get(EvalSet, run.eval_set_id)
    user_id = run.triggered_by
    tags = [f"eval_{eval_set.name}"] if eval_set is not None else []

    # Progress counters are shared across concurrent workers; the lock also
    # serializes DB writes, since one AsyncSession is not safe for concurrent use.
    state = {"done": 0, "correct": 0}
    lock = asyncio.Lock()
    concurrency = config.get("concurrency") or settings.run_concurrency
    semaphore = asyncio.Semaphore(max(concurrency, 1))

    async def process(item: dict) -> None:
        async with semaphore:
            await _process_question(
                session, run_id, item, total, state, lock, user_id, tags,
                seams, agent_timeout_s, cancel_event,
            )

    # return_exceptions=True: one unexpected per-question error must not cancel
    # its siblings. _process_question already handles the expected ones.
    results = await asyncio.gather(*(process(i) for i in snapshot), return_exceptions=True)
    for res in results:
        if isinstance(res, BaseException):
            log.exception("unexpected per-question error", exc_info=res)

    # Finalize aggregates (§6.13 card reads these directly).
    cancelled = cancel_event.is_set()
    correct = state["correct"]
    run.status = "cancelled" if cancelled else "completed"
    run.completed_at = datetime.now(timezone.utc)
    run.total_count = total
    run.correct_count = correct
    # A cancelled run has no meaningful pass rate: scoring a partial run against
    # the full question count would drag the eval set's trend line down for a
    # reason that has nothing to do with the agent. Left null, exactly as a
    # failed run already is.
    run.pass_rate = None if cancelled else ((correct / total) if total else None)
    await session.commit()

    await hub.publish(
        run_id,
        {
            "type": "run_completed",
            "status": run.status,
            "total": total,
            "correct": correct,
            "done": state["done"],
            "pass_rate": run.pass_rate,
        },
    )


async def _process_question(session, run_id, item, total, state, lock, user_id, tags,
                            seams: Seams, agent_timeout_s: float,
                            cancel_event: asyncio.Event) -> None:
    result: QuestionResult = item["result"]
    correlation_id = result.correlation_id

    # Not started yet when the stop button was hit: leave it 'pending' so the run
    # honestly reads as "stopped after N of M".
    if cancel_event.is_set():
        return

    # Flipped once span_analyses is written, so the final event tells the detail
    # view a diagnosis is now there to fetch.
    has_analysis = False

    async def publish(event_type: str) -> None:
        async with lock:
            snap = (state["done"], state["correct"])
        await _publish_progress(
            run_id, item["pk"], result, snap[0], total, snap[1], event_type,
            has_analysis=has_analysis,
        )

    async def fail(message: str) -> None:
        async with lock:
            result.status = "failed"
            result.error_message = clip(message)
            await session.commit()
            state["done"] += 1
            snap = (state["done"], state["correct"])
        await _publish_progress(run_id, item["pk"], result, snap[0], total, snap[1])

    async def cancel(message: str) -> None:
        """Stopped mid-flight. Not a failure of the agent — say so plainly, and
        don't count it as done: the progress bar should show where we stopped."""
        async with lock:
            result.status = "cancelled"
            result.error_message = message
            await session.commit()
            snap = (state["done"], state["correct"])
        await _publish_progress(run_id, item["pk"], result, snap[0], total, snap[1])

    await publish("question_started")

    # 1) agent
    try:
        agent_resp = await call_agent(
            seams, item["question"], correlation_id, user_id, tags,
            agent_timeout_s, cancel_event,
        )
    except RunCancelled:
        await cancel("Run cancelled while waiting for the agent.")
        return
    except Exception as exc:  # noqa: BLE001
        log.warning("agent call failed for %s: %s", correlation_id, exc)
        await fail(f"Agent call failed: {exc!s}")
        return

    if agent_resp.failed:
        async with lock:
            result.agent_response = agent_resp.response or None
            result.agent_latency_ms = agent_resp.latency_ms
        await fail(agent_resp.error or "Agent reported a failure.")
        return

    async with lock:
        result.agent_response = agent_resp.response
        result.agent_latency_ms = agent_resp.latency_ms
        await session.commit()
    await publish("question_answered")

    # 2) judge
    if cancel_event.is_set():
        await cancel("Run cancelled before judging; the agent's answer was kept.")
        return
    try:
        verdict = await call_judge(
            seams, item["question"], agent_resp.response, item["ground_truth"],
            cancel_event,
        )
    except RunCancelled:
        await cancel("Run cancelled while judging; the agent's answer was kept.")
        return
    except Exception as exc:  # noqa: BLE001
        log.warning("judge call failed for %s: %s", correlation_id, exc)
        # Deliberately NOT defaulted to 'correct' — an unjudged question is an
        # unknown, and silently passing it would inflate the pass rate.
        await fail(f"Judge call failed: {exc!s}")
        return

    async with lock:
        result.verdict = verdict.verdict
        result.judge_score = verdict.score
        result.judge_comment = verdict.comment
        result.status = "done"
        await session.commit()
        if verdict.verdict == "correct":
            state["correct"] += 1

    # Publish the verdict now rather than only after the trace poll and diagnosis
    # below, which together can run for tens of seconds against real services.
    # Without this the question sits on "judging…" long after it has been judged.
    await publish("question_judged")

    # 3) wait for trace ready (§6.12) before any diagnosis. A judged question is
    #    a finished question, so cancellation from here on keeps the verdict and
    #    just skips the extras.
    trace = None
    if not cancel_event.is_set():
        trace, trace_error = await wait_for_trace(correlation_id, seams.trace, cancel_event)
        async with lock:
            result.trace_ready = trace is not None
            result.trace_error = trace_error
            await session.commit()
        # The trace becoming available (or failing to) is itself a reason for the
        # middle column to refetch — diagnosis may still be minutes away.
        await publish("question_traced")

    # 4) diagnose incorrect questions once, at run time (§6.12 cache).
    #    A diagnosis failure leaves the question intact and undiagnosed; the
    #    reason is stored so the UI can explain the absence, and the owner can
    #    retry from the UI via re-diagnose.
    if verdict.verdict == "incorrect" and trace is not None and not cancel_event.is_set():
        try:
            diag = await run_diagnosis(seams, trace, item["reasoning"], verdict)
        except Exception as exc:  # noqa: BLE001
            log.warning("diagnosis failed for %s: %s", correlation_id, exc)
            async with lock:
                result.diagnosis_error = clip(f"{type(exc).__name__}: {exc}")
                await session.commit()
        else:
            async with lock:
                result.diagnosis_error = None
                session.add(
                    SpanAnalysis(
                        question_result_id=result.id,
                        overall_diagnosis=diag["overall_diagnosis"],
                        caveat=diag.get("caveat"),
                        raw_llm_output=diag,
                        model_used=seams.diagnosis.model_name,
                    )
                )
                await session.commit()
            has_analysis = True

    async with lock:
        state["done"] += 1
        snap = (state["done"], state["correct"])
    await _publish_progress(
        run_id, item["pk"], result, snap[0], total, snap[1],
        has_analysis=has_analysis,
    )


async def _finalize_failed(session, run_id: uuid.UUID, exc: Exception) -> None:
    """Close out a run that blew up, so the UI never waits on a dead stream."""
    try:
        # The session may be in a broken transaction; roll back and re-read the
        # run rather than touching the (now expired) instance we started with.
        await session.rollback()
        run = await session.get(Run, run_id)
        if run is not None:
            run.status = "failed"
            run.completed_at = datetime.now(timezone.utc)
            run.error_message = clip(f"{type(exc).__name__}: {exc}")
            await session.commit()
    except Exception:  # noqa: BLE001 - the SSE terminator still has to go out
        log.exception("could not persist failed state for run %s", run_id)
    await hub.publish(
        run_id,
        {"type": "run_completed", "status": "failed", "error": clip(str(exc))},
    )


async def _publish_progress(run_id, question_pk, result: QuestionResult,
                            done: int, total: int, correct: int,
                            event_type: str = "question_done",
                            has_analysis: bool = False) -> None:
    await hub.publish(
        run_id,
        {
            "type": event_type,
            "question_pk": str(question_pk),
            # The colour the left column should paint this question, derived in
            # one place (see services/aggregation.result_phase) so the API and
            # the live stream can never disagree.
            "phase": result_phase(result.status, result.agent_response, result.verdict),
            "verdict": result.verdict,
            "status": result.status,
            "error_message": result.error_message,
            "trace_ready": result.trace_ready,
            # The detail view refetches the trace when any of these change, so
            # the middle column follows a live question instead of freezing at
            # whatever it looked like when it was clicked. Without them the
            # diagnosis — written after the last payload would otherwise be
            # composed — only surfaces on the end-of-run reload.
            "has_analysis": has_analysis,
            "trace_error": result.trace_error,
            "diagnosis_error": result.diagnosis_error,
            "done": done,
            "total": total,
            "correct": correct,
        },
    )
