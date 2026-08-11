"""The playground: one ad-hoc question, run once, held in memory (§10).

Why this exists: after the three-column view shows that a question went wrong, the
developer usually has a hypothesis — "if the skill said X instead, this would have
worked", or "if the model were bigger". Before Stage 4 the only way to test that
hypothesis was to edit an eval set and run the whole thing. This is the cheap
path: one question, one editable copy of the agent's config and skill files, and
a button.

**Nothing here is persisted, and that is a decision, not an omission.** An attempt
is a scratch experiment; a run is a historical record. Keeping attempts out of the
database means no migration, no ownership rows, no "is this run real" ambiguity in
the eval history — and it costs exactly one thing, which the UI says plainly:
a backend restart loses them.

Consequences worth knowing about:

  * The store lives in this process. So does the SSE hub (§9.10), so this adds no
    new constraint — but both mean a multi-worker deployment would need a shared
    bus first.
  * One attempt holds a whole trace, which is hundreds of KB for a real agent
    (§9.19). Hence the per-subject cap: unbounded, this would leak the process's
    memory one attempt at a time.

The four steps and their retry/timeout/cancel policies are `app/pipeline.py`,
shared with the orchestrator. What differs here is what is optional: with no
expected answer nothing is judged, and with no expected reasoning nothing is
diagnosed (§10.4). Neither is an error — a developer trying a question out often
has no ground truth to offer, and demanding one would make the cheap path
expensive again.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app import cancellation
from app.config import settings
from app.integrations import Seams, build_seams
from app.integrations.base import Trace, Verdict, WorkspaceOverride
from app.pipeline import (
    RunCancelled,
    call_agent,
    call_judge,
    clip,
    run_diagnosis,
    wait_for_trace,
)
from app.services.failure_text import describe_failure
from app.sse import hub

log = logging.getLogger(__name__)

# Background tasks are kept referenced until they finish; asyncio only holds weak
# references, so a task nobody keeps can be garbage collected mid-flight.
_tasks: set[asyncio.Task] = set()


@dataclass
class PlaygroundAttempt:
    """One question sent to the agent, and everything that came back."""

    id: uuid.UUID
    subject: str
    question: str
    # Both optional (§10.4): the expected answer switches judging on, the
    # expected reasoning switches diagnosis on. Neither is required to ask a
    # question and read its trace.
    ground_truth_response: str | None
    ground_truth_reasoning: str | None
    workspace: WorkspaceOverride | None
    # What the agent server's skill files looked like when the override was
    # composed. Kept only to work out which files the developer actually
    # changed: `workspace.skills` is the complete set either way (§5.3 of the
    # agent-server contract), so without a baseline every file would look
    # edited.
    workspace_baseline: dict[str, str] | None
    # The effective settings, materialized at send time exactly as a run's are
    # (§9.15): a blank field is stored as the environment's value, so an attempt
    # is a complete record of what it used.
    config: dict
    # Write-only, the same rule as runs.secrets: no response model has a field
    # that could carry one outward.
    secrets: dict
    correlation_id: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    status: str = "running"  # running | done | failed | cancelled
    # pending -> answered -> judged -> traced -> diagnosed. 'judged' and
    # 'diagnosed' are skipped when the matching ground truth was not supplied.
    phase: str = "pending"

    agent_response: str | None = None
    # When the agent call actually went out, which is what the list's timer
    # counts from. Not the same as `created_at`: the attempt row exists from the
    # moment the request is accepted, and the workspace baseline lookup can sit
    # between the two. Server-side rather than noted by the browser, so the
    # elapsed time survives a reload and reads the same for a late subscriber.
    agent_started_at: datetime | None = None
    agent_latency_ms: int | None = None
    error_message: str | None = None
    # Which step failed and how, in the same vocabulary a run's question_results
    # use ('agent' | 'agent_timeout' | 'judge' | 'judge_timeout'). The message
    # already says it in words; this is what lets the list mark a timed-out
    # attempt without reading the sentence.
    failure_kind: str | None = None

    verdict: str | None = None
    judge_score: float | None = None
    judge_comment: str | None = None

    trace: Trace | None = None
    trace_error: str | None = None
    analysis: dict | None = None
    analysis_model: str | None = None
    analysis_generated_at: datetime | None = None
    diagnosis_error: str | None = None

    @property
    def judged(self) -> bool:
        """Whether an expected answer was supplied, i.e. whether judging applies."""
        return bool((self.ground_truth_response or "").strip())

    @property
    def diagnosable(self) -> bool:
        """Whether an expected process was supplied, i.e. whether diagnosis applies."""
        return bool((self.ground_truth_reasoning or "").strip())

    @property
    def config_overrides(self) -> list[str]:
        """Dotted config paths this attempt overrode, for the list's summary."""
        if self.workspace is None:
            return []
        return self.workspace.edited_config_paths

    @property
    def edited_skill_files(self) -> list[str]:
        """Skill files this attempt changed, deleted, or added.

        Computed against the snapshot the editor started from rather than
        against the agent server as it is now: the attempt records what the
        developer changed *at send time*, and the agent's own files may well
        have moved on since.
        """
        if self.workspace is None or self.workspace.skills is None:
            return []
        before, after = self.workspace_baseline or {}, self.workspace.skills
        changed = [p for p, text in after.items() if before.get(p) != text]
        deleted = [p for p in before if p not in after]
        return sorted(changed + deleted)


# Insertion-ordered so eviction is "drop the oldest" without sorting.
_store: OrderedDict[uuid.UUID, PlaygroundAttempt] = OrderedDict()


def add(attempt: PlaygroundAttempt) -> None:
    """Store an attempt, evicting this subject's oldest ones over the cap."""
    _store[attempt.id] = attempt
    cap = max(settings.playground_max_attempts_per_user, 1)
    mine = [a for a in _store.values() if a.subject == attempt.subject]
    for stale in mine[:-cap]:
        # Evicting a still-running attempt would orphan its background task, so
        # only finished ones are dropped. Someone who starts more than `cap`
        # attempts at once keeps them all until they finish.
        if stale.status == "running":
            continue
        _store.pop(stale.id, None)


def get(attempt_id: uuid.UUID, subject: str) -> PlaygroundAttempt | None:
    """One attempt, but only for the developer who created it.

    Another subject gets nothing back, and the router turns that into a 404
    rather than a 403: an attempt is private scratch work, so whether one exists
    at a given id is itself not theirs to learn.
    """
    attempt = _store.get(attempt_id)
    if attempt is None or attempt.subject != subject:
        return None
    return attempt


def remove(attempt_id: uuid.UUID, subject: str) -> None:
    """Forget one attempt. Only the owner's own, same rule as `get`."""
    attempt = _store.get(attempt_id)
    if attempt is not None and attempt.subject == subject:
        _store.pop(attempt_id, None)


def list_for(subject: str) -> list[PlaygroundAttempt]:
    """This subject's attempts, newest first."""
    return sorted(
        (a for a in _store.values() if a.subject == subject),
        key=lambda a: a.created_at,
        reverse=True,
    )


def clear() -> None:
    """Drop every attempt. For tests — nothing in the app calls this."""
    _store.clear()


def _verdict_of(attempt: PlaygroundAttempt) -> Verdict | None:
    """The attempt's verdict as the diagnosis contract wants it, or None.

    None is a real case, not a fallback: an expected process with no expected
    answer means the trace can be compared to a flow but nothing was graded, and
    the diagnosis prompt says so rather than letting the model assume the answer
    was wrong (§10.4).
    """
    if attempt.verdict is None:
        return None
    return Verdict(
        verdict=attempt.verdict,
        score=attempt.judge_score if attempt.judge_score is not None else 0.0,
        comment=attempt.judge_comment,
    )


def event_for(attempt: PlaygroundAttempt, event_type: str) -> dict:
    """One attempt's current state, in the shape the streams send.

    Shared with the per-user stream's snapshot (`routers/playground.py`) so a
    late subscriber and a live event can never describe an attempt differently —
    which matters more than it sounds, because reconciling on connect is the
    whole mechanism by which a reload recovers attempts it missed.

    The field names deliberately match the run stream's (§9.10): the front end
    refetches an open question whenever `phase`, `verdict`, `trace_ready` or
    `has_analysis` changes, and reusing that vocabulary means the playground gets
    the same event-driven refresh without a second mechanism.
    """
    return {
        "type": event_type,
        "attempt_id": str(attempt.id),
        "phase": attempt.phase,
        "status": attempt.status,
        "verdict": attempt.verdict,
        "error_message": attempt.error_message,
        "trace_ready": attempt.trace is not None,
        "has_analysis": attempt.analysis is not None,
        "trace_error": attempt.trace_error,
        "diagnosis_error": attempt.diagnosis_error,
        # The three timing fields the list row needs to finish itself. Carrying
        # them means a completed attempt no longer costs a full `listAttempts`
        # refetch purely to pick up its latency — which, once every attempt
        # reports rather than only the open one, would have been one extra
        # request per completion per person.
        "created_at": attempt.created_at.isoformat(),
        "agent_started_at": (
            attempt.agent_started_at.isoformat() if attempt.agent_started_at else None
        ),
        "agent_latency_ms": attempt.agent_latency_ms,
    }


async def _publish(attempt: PlaygroundAttempt, event_type: str) -> None:
    """Push the attempt's current state to its SSE subscribers.

    Published to **two topics**: the attempt's own id, and the subject who owns
    it. The per-attempt topic is what `GET /attempts/{id}/progress` serves; the
    per-subject topic is what lets one stream follow every attempt a person has
    running.

    That second topic is the fix for a real bug. With only the first, the front
    end could subscribe to one attempt at a time — so asking a second question
    closed the first one's stream, and everything that attempt published
    afterwards went to a topic with no subscribers and was silently dropped. The
    row then sat unfinished until it was clicked, which re-subscribed and pulled
    a fresh snapshot. Publishing to the owner as well makes "which attempt is
    open" irrelevant to whether its progress is delivered.
    """
    event = event_for(attempt, event_type)
    await hub.publish(attempt.id, event)
    await hub.publish(attempt.subject, event)


def start(attempt: PlaygroundAttempt) -> None:
    """Store the attempt and kick off its background execution."""
    add(attempt)
    task = asyncio.create_task(execute(attempt.id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def execute(attempt_id: uuid.UUID) -> None:
    """Run one attempt to completion. Never raises."""
    attempt = _store.get(attempt_id)
    if attempt is None:
        return
    try:
        await _execute(attempt)
    except Exception as exc:  # noqa: BLE001 - last line of defence
        log.exception("playground attempt %s failed", attempt_id)
        attempt.status = "failed"
        attempt.error_message = clip(f"{type(exc).__name__}: {exc}")
        # The terminal event still has to go out, or the UI waits on a stream
        # nobody will close — the same rule the orchestrator follows.
        await _publish(attempt, "attempt_completed")
    finally:
        cancellation.clear(attempt_id)


async def _execute(attempt: PlaygroundAttempt) -> None:
    cancel_event = cancellation.event_for(attempt.id)
    try:
        seams = build_seams(attempt.config, attempt.secrets)
    except Exception as exc:  # noqa: BLE001 - misconfiguration, not a bug
        # e.g. JUDGE_IMPL=real with no model. Reported on the attempt rather than
        # raised, so the developer reads the reason instead of a 500.
        attempt.status = "failed"
        attempt.error_message = clip(f"{type(exc).__name__}: {exc}")
        await _publish(attempt, "attempt_completed")
        return

    timeout_s = attempt.config.get("agent_timeout_s") or settings.agent_timeout_s
    # Stamped before the event goes out, so `attempt_started` already carries the
    # instant the list's timer counts from. Set here rather than at creation
    # because the workspace baseline lookup (§10, up to BASELINE_TIMEOUT_S) sits
    # between the two, and charging that to the agent would overstate every
    # attempt that sent a skill override.
    attempt.agent_started_at = datetime.now(timezone.utc)
    await _publish(attempt, "attempt_started")

    async def cancelled(message: str) -> None:
        attempt.status = "cancelled"
        attempt.error_message = message
        await _publish(attempt, "attempt_completed")

    # 1) agent
    # Measured rather than derived from the limit: the call is retried, so the
    # time a developer actually waited is a multiple of what they configured.
    agent_started = time.monotonic()
    try:
        agent_resp = await call_agent(
            seams, attempt.question, attempt.correlation_id, attempt.subject,
            ["playground"], timeout_s, cancel_event,
            workspace=attempt.workspace,
        )
    except RunCancelled:
        await cancelled("Stopped while waiting for the agent.")
        return
    except Exception as exc:  # noqa: BLE001
        log.warning("playground agent call failed for %s: %s", attempt.correlation_id, exc)
        message, kind = describe_failure(
            "agent", exc,
            timeout_s=timeout_s,
            attempts=settings.agent_max_retries + 1,
            waited_s=time.monotonic() - agent_started,
        )
        attempt.status = "failed"
        attempt.error_message = clip(message)
        attempt.failure_kind = kind
        await _publish(attempt, "attempt_completed")
        return

    attempt.agent_latency_ms = agent_resp.latency_ms
    if agent_resp.failed:
        attempt.agent_response = agent_resp.response or None
        attempt.status = "failed"
        attempt.error_message = clip(agent_resp.error or "Agent reported a failure.")
        await _publish(attempt, "attempt_completed")
        return

    attempt.agent_response = agent_resp.response
    attempt.phase = "answered"
    await _publish(attempt, "attempt_answered")

    # 2) judge — only when there is an expected answer to grade against.
    if attempt.judged and not cancel_event.is_set():
        judge_started = time.monotonic()
        try:
            verdict = await call_judge(
                seams, attempt.question, agent_resp.response,
                attempt.ground_truth_response or "", cancel_event,
            )
        except RunCancelled:
            await cancelled("Stopped while judging; the agent's answer was kept.")
            return
        except Exception as exc:  # noqa: BLE001
            # Unlike a run, a failed judge does not fail the attempt: the answer
            # and the trace are still worth reading, and the developer can see
            # why there is no verdict. Nothing aggregates these numbers, so
            # there is no pass rate to inflate.
            log.warning("playground judge call failed for %s: %s", attempt.correlation_id, exc)
            message, kind = describe_failure(
                "judge", exc,
                timeout_s=settings.llm_timeout_s,
                attempts=settings.llm_max_retries + 1,
                waited_s=time.monotonic() - judge_started,
            )
            attempt.error_message = clip(message)
            attempt.failure_kind = kind
        else:
            attempt.verdict = verdict.verdict
            attempt.judge_score = verdict.score
            attempt.judge_comment = verdict.comment
            attempt.phase = "judged"
        await _publish(attempt, "attempt_judged")

    # 3) wait for the trace to land (§6.12).
    if not cancel_event.is_set():
        trace, trace_error = await wait_for_trace(
            attempt.correlation_id, seams.trace, cancel_event
        )
        attempt.trace = trace
        attempt.trace_error = trace_error
        if trace is not None:
            attempt.phase = "traced"
        await _publish(attempt, "attempt_traced")

    # 4) diagnose — only when there is an expected process to compare against.
    #    Unlike a run this is not gated on an incorrect verdict: a developer who
    #    described the expected flow wants to know where the trace diverged from
    #    it, and may not have supplied an expected answer at all.
    if attempt.diagnosable and attempt.trace is not None and not cancel_event.is_set():
        try:
            diag = await run_diagnosis(
                seams, attempt.trace, attempt.ground_truth_reasoning or "",
                _verdict_of(attempt),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("playground diagnosis failed for %s: %s", attempt.correlation_id, exc)
            attempt.diagnosis_error = clip(f"{type(exc).__name__}: {exc}")
        else:
            attempt.diagnosis_error = None
            attempt.analysis = diag
            attempt.analysis_model = seams.diagnosis.model_name
            attempt.analysis_generated_at = datetime.now(timezone.utc)
            attempt.phase = "diagnosed"

    attempt.status = "cancelled" if cancel_event.is_set() else "done"
    await _publish(attempt, "attempt_completed")


async def re_diagnose(attempt: PlaygroundAttempt, seams: Seams) -> dict:
    """Regenerate the diagnosis for an attempt whose trace is already in hand.

    Raises for the router to map: the caller needs the model's own error message,
    not a 500 with the reason in a log file.
    """
    if attempt.trace is None:
        raise ValueError("this attempt has no trace to diagnose")
    if not attempt.diagnosable:
        raise ValueError("supply an expected reasoning process to diagnose an attempt")

    diag = await run_diagnosis(
        seams, attempt.trace, attempt.ground_truth_reasoning or "", _verdict_of(attempt)
    )
    attempt.analysis = diag
    attempt.analysis_model = seams.diagnosis.model_name
    attempt.analysis_generated_at = datetime.now(timezone.utc)
    attempt.diagnosis_error = None
    if attempt.phase == "traced":
        attempt.phase = "diagnosed"
    return diag
