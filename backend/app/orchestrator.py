"""Run orchestrator (§6.15).

Runs as a background asyncio task with its own DB session. Per question (from a
snapshot read at run start):

    gen correlation_id -> agent (correlation_id in request metadata)
      -> judge -> write question_results
      -> poll trace until ready (backoff) -> set trace_ready
      -> if incorrect: fetch+truncate trace -> diagnose -> write span_analyses
      -> push live progress (SSE)

Failure policy, which matters once the seams are real services rather than
fakes that never raise:

  * A question that fails (agent error, judge error, timeout) is marked
    status='failed' with an error_message, and the run continues — partial
    completion, as before.
  * A diagnosis failure never fails the question. The verdict is the result;
    the diagnosis is an extra.
  * Any unexpected error still finalizes the run (status='failed') and still
    publishes a terminal SSE event. A run must never be left stuck in
    'running' with a stream nobody will ever close.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.integrations import agent_client, diagnosis_client, judge_client, trace_client
from app.integrations.base import NotReady
from app.models import EvalSet, Question, QuestionResult, Run, SpanAnalysis
from app.sse import hub

log = logging.getLogger(__name__)

# Errors worth retrying: transient transport/server problems. A bad request or a
# malformed judge response will fail the same way every time, so those bubble up
# on the first attempt.
_RETRYABLE = (asyncio.TimeoutError, ConnectionError, OSError)


def _clip(message: str) -> str:
    return message[: settings.error_message_max_chars]


async def _with_retries(coro_factory, attempts: int, what: str):
    """Run an awaitable factory with bounded exponential backoff."""
    last: Exception | None = None
    for attempt in range(max(attempts, 0) + 1):
        try:
            return await coro_factory()
        except _RETRYABLE as exc:  # noqa: PERF203 - retry loop
            last = exc
            if attempt >= attempts:
                break
            delay = 2.0**attempt
            log.warning("%s failed (%s); retrying in %.1fs", what, exc, delay)
            await asyncio.sleep(delay)
    raise last if last is not None else RuntimeError(f"{what} failed")


async def _poll_trace_ready(correlation_id: str):
    """Poll the trace store with backoff until ready or capped (§6.12)."""
    backoff = settings.trace_poll_backoff_s or [1.0]
    for attempt in range(settings.trace_poll_max_attempts):
        try:
            trace = await trace_client.fetch_trace(correlation_id)
        except Exception as exc:  # trace store hiccup must not fail the question
            log.warning("fetch_trace(%s) failed: %s", correlation_id, exc)
            trace = None
        if trace is not None and not isinstance(trace, NotReady):
            return trace
        await asyncio.sleep(backoff[min(attempt, len(backoff) - 1)])
    return None


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


async def _execute_run(session, run: Run) -> None:
    run_id = run.id

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
    semaphore = asyncio.Semaphore(max(settings.run_concurrency, 1))

    async def process(item: dict) -> None:
        async with semaphore:
            await _process_question(session, run_id, item, total, state, lock, user_id, tags)

    # return_exceptions=True: one unexpected per-question error must not cancel
    # its siblings. _process_question already handles the expected ones.
    results = await asyncio.gather(*(process(i) for i in snapshot), return_exceptions=True)
    for res in results:
        if isinstance(res, BaseException):
            log.exception("unexpected per-question error", exc_info=res)

    # Finalize aggregates (§6.13 card reads these directly).
    correct = state["correct"]
    run.status = "completed"
    run.completed_at = datetime.now(timezone.utc)
    run.total_count = total
    run.correct_count = correct
    run.pass_rate = (correct / total) if total else None
    await session.commit()

    await hub.publish(
        run_id,
        {
            "type": "run_completed",
            "total": total,
            "correct": correct,
            "pass_rate": run.pass_rate,
        },
    )


async def _process_question(session, run_id, item, total, state, lock, user_id, tags) -> None:
    correlation_id = uuid.uuid4().hex
    async with lock:
        result = QuestionResult(
            run_id=run_id,
            question_pk=item["pk"],
            correlation_id=correlation_id,
            status="pending",
            trace_ready=False,
        )
        session.add(result)
        await session.commit()

    async def fail(message: str) -> None:
        async with lock:
            result.status = "failed"
            result.error_message = _clip(message)
            await session.commit()
            state["done"] += 1
            snap = (state["done"], state["correct"])
        await _publish_progress(run_id, item["pk"], result, snap[0], total, snap[1])

    # 1) agent
    try:
        agent_resp = await _with_retries(
            lambda: asyncio.wait_for(
                agent_client.call(item["question"], correlation_id, user_id, tags),
                timeout=settings.agent_timeout_s,
            ),
            settings.agent_max_retries,
            "agent call",
        )
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

    # 2) judge
    try:
        verdict = await _with_retries(
            lambda: judge_client.judge(
                item["question"], agent_resp.response, item["ground_truth"]
            ),
            settings.llm_max_retries,
            "judge call",
        )
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

    # 3) wait for trace ready (§6.12) before any diagnosis
    trace = await _poll_trace_ready(correlation_id)
    if trace is not None:
        async with lock:
            result.trace_ready = True
            await session.commit()

    # 4) diagnose incorrect questions once, at run time (§6.12 cache).
    #    A diagnosis failure leaves the question intact and undiagnosed; the
    #    owner can retry from the UI via re-diagnose.
    if verdict.verdict == "incorrect" and trace is not None:
        try:
            diag = await diagnosis_client.diagnose(trace, item["reasoning"], verdict)
        except Exception as exc:  # noqa: BLE001
            log.warning("diagnosis failed for %s: %s", correlation_id, exc)
        else:
            async with lock:
                session.add(
                    SpanAnalysis(
                        question_result_id=result.id,
                        overall_diagnosis=diag["overall_diagnosis"],
                        caveat=diag.get("caveat"),
                        raw_llm_output=diag,
                        model_used=diagnosis_client.model_name,
                    )
                )
                await session.commit()

    async with lock:
        state["done"] += 1
        snap = (state["done"], state["correct"])
    await _publish_progress(run_id, item["pk"], result, snap[0], total, snap[1])


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
            run.error_message = _clip(f"{type(exc).__name__}: {exc}")
            await session.commit()
    except Exception:  # noqa: BLE001 - the SSE terminator still has to go out
        log.exception("could not persist failed state for run %s", run_id)
    await hub.publish(
        run_id,
        {"type": "run_completed", "status": "failed", "error": _clip(str(exc))},
    )


async def _publish_progress(run_id, question_pk, result: QuestionResult,
                            done: int, total: int, correct: int) -> None:
    await hub.publish(
        run_id,
        {
            "type": "question_done",
            "question_pk": str(question_pk),
            "verdict": result.verdict,
            "status": result.status,
            "error_message": result.error_message,
            "trace_ready": result.trace_ready,
            "done": done,
            "total": total,
            "correct": correct,
        },
    )
