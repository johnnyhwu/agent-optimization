"""Playground endpoints (§10): one ad-hoc question at a time.

Two things differ from every other router here, both because an attempt is not an
eval set:

**Authorization.** `require_owner` / `require_reader` both declare an
`eval_set_id` path parameter, so neither applies. The rule is simply that an
attempt belongs to whoever created it, and another subject gets **404, not 403** —
scratch work is private, so whether an attempt exists at a given id is not
theirs to learn either.

**No database.** Attempts live in `app/playground.py`'s in-memory store. There is
no migration, no ownership table and no row to clean up; the cost is that a
backend restart loses them, which the UI states plainly.
"""
from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sse_starlette.sse import EventSourceResponse

from app import cancellation, playground
from app.auth import current_subject
from app.integrations import build_seams
from app.integrations.base import SkillOverride
from app.playground import PlaygroundAttempt
from app.schemas import (
    AnalysisOut,
    PlaygroundAttemptDetail,
    PlaygroundAttemptOut,
    PlaygroundCreate,
    RunConfig,
    SkillOut,
    SkillSummaryOut,
    SuspectOut,
    TraceView,
)
from app.services import run_config
from app.services.trace_view import span_to_out
from app.sse import hub

router = APIRouter(prefix="/playground", tags=["playground"])


# --- Skills -----------------------------------------------------------------

def _skill_client():
    """The skill seam, or a 503 explaining why there isn't one.

    `include_skill=True` is what makes `build_seams` construct it at all — a
    misconfigured skill seam must not be able to break the eval path, so nothing
    else asks for it. `SKILL_IMPL=real` with no agent base URL raises here, and
    the developer needs to read that sentence rather than get a 500.
    """
    try:
        seams = build_seams(include_skill=True)
    except Exception as exc:  # noqa: BLE001 - misconfiguration, not a server bug
        raise HTTPException(
            status_code=503, detail=f"{type(exc).__name__}: {exc}"
        ) from exc
    if seams.skill is None:  # pragma: no cover - include_skill guarantees one
        raise HTTPException(status_code=503, detail="no skill client configured")
    return seams.skill


@router.get("/skills", response_model=list[SkillSummaryOut])
async def list_skills(subject: str = Depends(current_subject)):
    """The agent's skill catalogue, so an edit starts from the real text.

    A failure is a 503 with the reason, never an empty list: "this agent has no
    skills" and "the agent server refused us" must not look the same, or the
    developer silently loses the starting point and pastes from memory instead.
    """
    client = _skill_client()
    try:
        skills = await client.list_skills()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=503, detail=f"could not read the skill catalogue: {exc}"
        ) from exc
    return [
        SkillSummaryOut(name=s.name, description=s.description) for s in skills
    ]


@router.get("/skills/{name}", response_model=SkillOut)
async def get_skill(name: str, subject: str = Depends(current_subject)):
    client = _skill_client()
    try:
        skill = await client.get_skill(name)
    except KeyError as exc:  # the fake client's "no such skill"
        raise HTTPException(status_code=404, detail=f"no such skill: {name}") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=503, detail=f"could not read skill {name!r}: {exc}"
        ) from exc
    return SkillOut(name=skill.name, content=skill.content, description=skill.description)


# --- Attempts ---------------------------------------------------------------

def _out(attempt: PlaygroundAttempt) -> PlaygroundAttemptOut:
    return PlaygroundAttemptOut(
        id=attempt.id,
        created_at=attempt.created_at,
        question=attempt.question,
        has_expected_answer=attempt.judged,
        has_expected_reasoning=attempt.diagnosable,
        skill_name=attempt.skill_name,
        skill_overridden=attempt.skill_override is not None,
        status=attempt.status,
        phase=attempt.phase,
        verdict=attempt.verdict,
        judge_score=attempt.judge_score,
        agent_latency_ms=attempt.agent_latency_ms,
        error_message=attempt.error_message,
        config=RunConfig(**(attempt.config or {})),
    )


def _analysis_out(attempt: PlaygroundAttempt) -> AnalysisOut | None:
    if attempt.analysis is None:
        return None
    return AnalysisOut(
        overall_diagnosis=attempt.analysis.get("overall_diagnosis", ""),
        caveat=attempt.analysis.get("caveat"),
        suspects=[SuspectOut(**s) for s in attempt.analysis.get("suspects", [])],
        generated_at=attempt.analysis_generated_at or attempt.created_at,
        model_used=attempt.analysis_model or "",
    )


def _trace_view(attempt: PlaygroundAttempt) -> TraceView:
    """The attempt's trace in the run detail view's shape.

    The trace is held on the attempt rather than refetched: it was already polled
    once during execution, and unlike a run there is no long-lived row to come
    back to days later — an attempt lives as long as the tab does.

    The five states mean here what they mean for a run (§9.5), which is why the
    same banners work:
      not_started  the agent hasn't answered yet
      no_trace     the attempt failed or was stopped before answering
      error        the trace store refused or could not be reached
      generating   answered, but ingestion hasn't landed
      ready        spans below
    """
    if attempt.trace is not None:
        state, error = "ready", None
    elif attempt.agent_response is None and attempt.status in ("failed", "cancelled"):
        state, error = "no_trace", attempt.trace_error
    elif attempt.phase == "pending":
        state, error = "not_started", None
    elif attempt.trace_error:
        state, error = "error", attempt.trace_error
    else:
        state, error = "generating", None

    return TraceView(
        trace_state=state,
        trace_error=error,
        diagnosis_error=attempt.diagnosis_error,
        spans=[span_to_out(s) for s in attempt.trace.spans] if attempt.trace else [],
        analysis=_analysis_out(attempt),
        verdict=attempt.verdict,
        judge_comment=attempt.judge_comment,
        agent_response=attempt.agent_response,
        ground_truth_response=attempt.ground_truth_response,
        ground_truth_reasoning=attempt.ground_truth_reasoning,
        error_message=attempt.error_message,
    )


def _detail(attempt: PlaygroundAttempt) -> PlaygroundAttemptDetail:
    return PlaygroundAttemptDetail(
        **_out(attempt).model_dump(),
        ground_truth_response=attempt.ground_truth_response,
        ground_truth_reasoning=attempt.ground_truth_reasoning,
        skill_content=attempt.skill_override.content if attempt.skill_override else None,
        trace=_trace_view(attempt),
    )


def _load(attempt_id: uuid.UUID, subject: str) -> PlaygroundAttempt:
    attempt = playground.get(attempt_id, subject)
    if attempt is None:
        # 404 for someone else's attempt as well as a missing one — see the module
        # docstring. Also what a developer sees after a backend restart dropped
        # the store, which the UI explains.
        raise HTTPException(status_code=404, detail="attempt not found")
    return attempt


@router.post("/attempts", response_model=PlaygroundAttemptDetail, status_code=201)
async def create_attempt(
    body: PlaygroundCreate,
    subject: str = Depends(current_subject),
):
    override = None
    if body.skill_override is not None:
        override = SkillOverride(
            name=body.skill_override.name, content=body.skill_override.content
        )

    attempt = PlaygroundAttempt(
        id=uuid.uuid4(),
        subject=subject,
        question=body.question,
        ground_truth_response=(body.ground_truth_response or "").strip() or None,
        ground_truth_reasoning=(body.ground_truth_reasoning or "").strip() or None,
        skill_override=override,
        # Materialized now, exactly as a run's is (§9.15): a blank field records
        # the environment's value, so the attempt says what it actually used.
        config=run_config.resolve(body.config),
        secrets={k: v for k, v in body.secrets.model_dump().items() if v},
        correlation_id=uuid.uuid4().hex,
    )
    playground.start(attempt)
    return _detail(attempt)


@router.get("/attempts", response_model=list[PlaygroundAttemptOut])
async def list_attempts(subject: str = Depends(current_subject)):
    """This subject's attempts, newest first — the left column's list.

    Not paginated: the store is capped per subject (§10.3), so there is a small
    fixed maximum by construction.
    """
    return [_out(a) for a in playground.list_for(subject)]


@router.get("/attempts/{attempt_id}", response_model=PlaygroundAttemptDetail)
async def get_attempt(
    attempt_id: uuid.UUID,
    subject: str = Depends(current_subject),
):
    return _detail(_load(attempt_id, subject))


@router.post("/attempts/{attempt_id}/cancel", response_model=PlaygroundAttemptOut)
async def cancel_attempt(
    attempt_id: uuid.UUID,
    subject: str = Depends(current_subject),
):
    attempt = _load(attempt_id, subject)
    if attempt.status != "running":
        raise HTTPException(status_code=409, detail=f"attempt is already {attempt.status}")
    # Signalling the event abandons the in-flight agent/judge call rather than
    # waiting for it (§9.17a) — otherwise "stop" would mean "stop in up to
    # AGENT_TIMEOUT_S", which is exactly when the button gets pressed.
    cancellation.signal(attempt_id)
    return _out(attempt)


@router.delete("/attempts/{attempt_id}", status_code=204)
async def delete_attempt(
    attempt_id: uuid.UUID,
    subject: str = Depends(current_subject),
):
    attempt = _load(attempt_id, subject)
    if attempt.status == "running":
        raise HTTPException(status_code=409, detail="stop the attempt before deleting it")
    playground.remove(attempt_id, subject)
    return Response(status_code=204)


@router.post("/attempts/{attempt_id}/re-diagnose", response_model=AnalysisOut)
async def re_diagnose_attempt(
    attempt_id: uuid.UUID,
    subject: str = Depends(current_subject),
):
    attempt = _load(attempt_id, subject)
    try:
        seams = build_seams(attempt.config, attempt.secrets)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}") from exc

    try:
        await playground.re_diagnose(attempt, seams)
    except ValueError as exc:
        # No trace yet, or no expected reasoning to compare against: the request
        # is premature rather than wrong, and the message says which.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        message = f"{type(exc).__name__}: {exc}"
        attempt.diagnosis_error = message
        raise HTTPException(status_code=502, detail=f"diagnosis failed: {message}") from exc

    analysis = _analysis_out(attempt)
    if analysis is None:  # pragma: no cover - re_diagnose sets it or raises
        raise HTTPException(status_code=502, detail="diagnosis produced nothing")
    return analysis


@router.get("/attempts/{attempt_id}/progress")
async def attempt_progress(
    attempt_id: uuid.UUID,
    request: Request,
    subject: str = Depends(current_subject),
):
    """SSE stream for one attempt.

    Same shape as the run stream (§9.10): subscribe before the generator starts so
    nothing published between authorization and the first yield is lost, send a
    snapshot for a late subscriber, keepalive every 15s, and always unsubscribe.
    """
    attempt = _load(attempt_id, subject)
    queue = hub.subscribe(attempt_id)

    async def event_gen():
        try:
            yield {
                "event": "snapshot",
                "data": json.dumps({
                    "attempt_id": str(attempt.id),
                    "status": attempt.status,
                    "phase": attempt.phase,
                    "verdict": attempt.verdict,
                    "trace_ready": attempt.trace is not None,
                    "has_analysis": attempt.analysis is not None,
                    "error_message": attempt.error_message,
                    "trace_error": attempt.trace_error,
                    "diagnosis_error": attempt.diagnosis_error,
                }),
            }
            if attempt.status != "running":
                yield {"event": "attempt_completed",
                       "data": json.dumps({"status": attempt.status})}
                return

            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": "{}"}
                    continue
                yield {"event": event.get("type", "message"), "data": json.dumps(event)}
                if event.get("type") == "attempt_completed":
                    break
        finally:
            hub.unsubscribe(attempt_id, queue)

    return EventSourceResponse(event_gen())
