"""The four steps of answering ONE question, and the policies around them.

Extracted from the orchestrator when the playground (§10) arrived and needed the
same sequence for a single ad-hoc question. What lives here is everything that is
true of *a question*:

    agent -> judge -> wait for the trace to land -> diagnose

...together with the three policies wrapped around those calls — bounded retries
for transient failures, a hard timeout on the agent, and racing every call
against a cancel event so "stop" means stop now rather than in up to
`AGENT_TIMEOUT_S`.

What deliberately stayed in `orchestrator.py`: writing `question_results` rows and
publishing run progress. Those are properties of *a run* — a row per question, a
done/total counter, an eval set. The playground has none of them, and forcing a
shared shape onto both would have meant inventing a fake run to hold one
throwaway question.

Nothing here touches the database or the SSE hub. Every function takes the seams
it needs and returns a value.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging

from app.config import settings
from app.integrations import Seams
from app.integrations.base import (
    AgentResponse,
    NotReady,
    Trace,
    Verdict,
    WorkspaceOverride,
)

log = logging.getLogger(__name__)

# Errors worth retrying: transient transport/server problems. A bad request or a
# malformed judge response will fail the same way every time, so those bubble up
# on the first attempt.
RETRYABLE = (asyncio.TimeoutError, ConnectionError, OSError)


class RunCancelled(Exception):
    """Raised inside a question when the run (or attempt) was cancelled mid-call."""


def clip(message: str) -> str:
    return message[: settings.error_message_max_chars]


async def with_retries(coro_factory, attempts: int, what: str):
    """Run an awaitable factory with bounded exponential backoff."""
    last: Exception | None = None
    for attempt in range(max(attempts, 0) + 1):
        try:
            return await coro_factory()
        except RETRYABLE as exc:  # noqa: PERF203 - retry loop
            last = exc
            if attempt >= attempts:
                break
            delay = 2.0**attempt
            log.warning("%s failed (%s); retrying in %.1fs", what, exc, delay)
            await asyncio.sleep(delay)
    raise last if last is not None else RuntimeError(f"{what} failed")


async def await_or_cancel(coro, cancel_event: asyncio.Event):
    """Await `coro`, abandoning it the moment cancellation is signalled.

    Waiting for the current call to return would make "stop" mean "stop in up to
    two minutes" against a real agent, so the in-flight task is cancelled rather
    than awaited.
    """
    task = asyncio.ensure_future(coro)
    waiter = asyncio.ensure_future(cancel_event.wait())
    try:
        await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        waiter.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await waiter

    if task.done():
        return task.result()

    task.cancel()
    with contextlib.suppress(BaseException):
        await task
    raise RunCancelled()


async def settle_trace(
    correlation_id: str, trace_client, trace: Trace, cancel_event: asyncio.Event
) -> Trace:
    """Re-read a trace that has started arriving until its span list stops growing.

    "The trace exists" and "the trace is complete" are different questions, and
    only the first one has an endpoint. Langfuse ingestion is incremental, so the
    read that first returns observations can land mid-flush — and the span that
    loses that race is systematically the *last* one, the agent's final answer
    generation, because it finishes immediately before the HTTP response that
    sends this platform looking for the trace.

    That matters twice over. The span list is what the developer reads, and it is
    also what the diagnosis compares against the expected process (§6.9): a trace
    silently missing its final step invites a diagnosis of a failure that never
    happened. In the playground it is worse still, because the attempt keeps the
    trace it was handed (§10) — nothing re-reads it later, so a short read stays
    short for as long as the attempt exists.

    Growth is the signal: a read that returns no new spans is taken as the end of
    ingestion. Equal-length reads still replace the previous one, since a later
    read of the same spans has the more complete bodies. A read that comes back
    *shorter*, errors, or says NotReady is discarded — settling may only ever add
    to what is already in hand, never take away.
    """
    reads = max(settings.trace_settle_max_reads, 0)
    delay = max(settings.trace_settle_delay_s, 0.0)
    best = trace
    for _ in range(reads):
        if cancel_event.is_set():
            return best
        await asyncio.sleep(delay)
        try:
            candidate = await trace_client.fetch_trace(correlation_id)
        except Exception as exc:  # noqa: BLE001 - we already have a usable trace
            log.warning("settle fetch_trace(%s) failed: %s", correlation_id, exc)
            return best
        if candidate is None or isinstance(candidate, NotReady):
            # Reading a trace that was there a moment ago and is now "not ready"
            # is a trace store being odd, not a reason to throw away good spans.
            return best
        if len(candidate.spans) < len(best.spans):
            return best
        grew = len(candidate.spans) > len(best.spans)
        best = candidate
        if not grew:
            return best
    log.info(
        "trace %s was still growing after %d settle reads; using %d spans",
        correlation_id, reads, len(best.spans),
    )
    return best


async def wait_for_trace(correlation_id: str, trace_client, cancel_event: asyncio.Event):
    """Poll the trace store with backoff until ready or capped (§6.12), then let
    it settle (§6.12a).

    Returns (trace_or_None, last_error_or_None). The error is kept rather than
    only logged: without it the UI cannot tell a misconfigured trace store from
    ingestion that simply hasn't landed yet.
    """
    backoff = settings.trace_poll_backoff_s or [1.0]
    last_error: str | None = None
    for attempt in range(settings.trace_poll_max_attempts):
        if cancel_event.is_set():
            return None, last_error
        try:
            trace = await trace_client.fetch_trace(correlation_id)
        except Exception as exc:  # trace store hiccup must not fail the question
            log.warning("fetch_trace(%s) failed: %s", correlation_id, exc)
            last_error = clip(f"{type(exc).__name__}: {exc}")
            trace = None
        else:
            last_error = None
        if trace is not None and not isinstance(trace, NotReady):
            return await settle_trace(correlation_id, trace_client, trace, cancel_event), None
        await asyncio.sleep(backoff[min(attempt, len(backoff) - 1)])
    return None, last_error


# --- The four steps ---------------------------------------------------------

async def call_agent(
    seams: Seams,
    question: str,
    correlation_id: str,
    user_id: str,
    tags: list[str] | None,
    timeout_s: float,
    cancel_event: asyncio.Event,
    workspace: WorkspaceOverride | None = None,
) -> AgentResponse:
    """Ask the agent, with the retry / timeout / cancel policies applied.

    Raises `RunCancelled` if stopped mid-call; any other exception is the caller's
    to record — how a failure is *stored* differs between a run and an attempt,
    but what counts as one does not.
    """
    # The keyword is only passed when there is one, so an eval run's call is
    # exactly the call it was before the playground existed — including for an
    # AgentClient implementation that never grew the parameter.
    extra = {} if workspace is None else {"workspace": workspace}
    return await await_or_cancel(
        with_retries(
            lambda: asyncio.wait_for(
                seams.agent.call(question, correlation_id, user_id, tags, **extra),
                timeout=timeout_s,
            ),
            settings.agent_max_retries,
            "agent call",
        ),
        cancel_event,
    )


async def call_judge(
    seams: Seams,
    question: str,
    response: str,
    ground_truth: str,
    cancel_event: asyncio.Event,
) -> Verdict:
    """Grade an answer. A failure here is never silently treated as 'correct' —
    callers record it as a failure, because an unjudged answer is an unknown."""
    return await await_or_cancel(
        with_retries(
            lambda: seams.judge.judge(question, response, ground_truth),
            settings.llm_max_retries,
            "judge call",
        ),
        cancel_event,
    )


async def run_diagnosis(
    seams: Seams,
    trace: Trace,
    ground_truth_reasoning: str,
    verdict: Verdict | None,
) -> dict:
    """Diagnose a trace against the expected process (§6.9).

    No retries and no cancel race, matching the run path: the diagnosis is an
    extra on top of a finished question, so a failure is recorded and life goes
    on. `verdict` may be None when nothing was graded (§10.4).
    """
    return await seams.diagnosis.diagnose(trace, ground_truth_reasoning, verdict)
