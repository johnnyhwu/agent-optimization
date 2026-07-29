"""Owner-only manual re-diagnose (§6.12 / §6.16).

Stage 1's only re-compute trigger. Viewer role is blocked by require_owner
(avoids LLM cost). Re-fetches the trace and regenerates span_analyses (upsert).
"""
from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_owner
from app.config import settings
from app.db import get_session
from app.integrations import build_seams
from app.integrations.base import NotReady, Verdict
from app.models import Question, QuestionResult, Run, SpanAnalysis
from app.schemas import AnalysisOut, SuspectOut

router = APIRouter(prefix="/eval-sets/{eval_set_id}", tags=["diagnosis"])


async def _resolve_trace(correlation_id: str, trace_client):
    """Poll the trace store until ingestion lands (§6.12). Short sleeps: this is
    a request path, and a still-missing trace returns 409 for the user to retry.

    Returns (trace_or_None, error_or_None) so the 409 can say *why* — "Langfuse
    refused the key" and "ingestion is a few seconds behind" call for very
    different reactions from the developer."""
    for _ in range(settings.trace_poll_max_attempts):
        try:
            trace = await trace_client.fetch_trace(correlation_id)
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            return None, f"{type(exc).__name__}: {exc}"
        if not isinstance(trace, NotReady):
            return trace, None
        await asyncio.sleep(0.05)
    return None, None


@router.post("/results/{result_id}/re-diagnose", response_model=AnalysisOut)
async def re_diagnose(
    eval_set_id: uuid.UUID,
    result_id: uuid.UUID,
    subject: str = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
):
    result = await session.get(QuestionResult, result_id)
    if result is None:
        raise HTTPException(status_code=404, detail="result not found")
    if result.verdict != "incorrect":
        raise HTTPException(status_code=400, detail="only incorrect questions are diagnosed")

    # Re-diagnose against the endpoints the run itself used, not whatever the
    # environment happens to point at now.
    run = await session.get(Run, result.run_id)
    try:
        seams = build_seams(run.config if run else None, run.secrets if run else None)
    except Exception as exc:  # noqa: BLE001 - misconfiguration, not a server bug
        raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}") from exc

    trace, trace_error = await _resolve_trace(result.correlation_id, seams.trace)
    if trace is None:
        detail = (
            f"could not fetch the trace: {trace_error}"
            if trace_error
            else "trace not ready yet; retry shortly"
        )
        raise HTTPException(status_code=409, detail=detail)

    question = await session.get(Question, result.question_pk)
    verdict = Verdict(
        verdict=result.verdict,
        score=float(result.judge_score) if result.judge_score is not None else 0.0,
        comment=result.judge_comment,
    )
    try:
        diag = await seams.diagnosis.diagnose(trace, question.ground_truth_reasoning, verdict)
    except Exception as exc:  # noqa: BLE001
        # The diagnosis model's own error is the useful part; a 500 would bury it
        # in the backend log and tell the developer nothing.
        message = f"{type(exc).__name__}: {exc}"
        result.diagnosis_error = message[: settings.error_message_max_chars]
        await session.commit()
        raise HTTPException(status_code=502, detail=f"diagnosis failed: {message}") from exc
    result.diagnosis_error = None

    existing = await session.scalar(
        select(SpanAnalysis).where(SpanAnalysis.question_result_id == result_id)
    )
    if existing is None:
        existing = SpanAnalysis(question_result_id=result_id)
        session.add(existing)
    existing.overall_diagnosis = diag["overall_diagnosis"]
    existing.caveat = diag.get("caveat")
    existing.raw_llm_output = diag
    existing.model_used = seams.diagnosis.model_name
    if not result.trace_ready:
        result.trace_ready = True
    await session.commit()
    await session.refresh(existing)

    return AnalysisOut(
        overall_diagnosis=existing.overall_diagnosis,
        caveat=existing.caveat,
        suspects=[SuspectOut(**s) for s in diag.get("suspects", [])],
        generated_at=existing.generated_at,
        model_used=existing.model_used,
    )
