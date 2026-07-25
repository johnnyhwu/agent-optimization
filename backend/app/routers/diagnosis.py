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

from app import fake_config as fc
from app.auth import require_owner
from app.db import get_session
from app.integrations import diagnosis_client, trace_client
from app.integrations.base import NotReady, Verdict
from app.models import Question, QuestionResult, SpanAnalysis
from app.schemas import AnalysisOut, SuspectOut

router = APIRouter(prefix="/eval-sets/{eval_set_id}", tags=["diagnosis"])


async def _resolve_trace(correlation_id: str):
    """Poll the (fake) trace store until ingestion lands (§6.12)."""
    for _ in range(fc.TRACE_POLL_MAX_ATTEMPTS):
        trace = await trace_client.fetch_trace(correlation_id)
        if not isinstance(trace, NotReady):
            return trace
        await asyncio.sleep(0.05)
    return None


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

    trace = await _resolve_trace(result.correlation_id)
    if trace is None:
        raise HTTPException(status_code=409, detail="trace not ready yet; retry shortly")

    question = await session.get(Question, result.question_pk)
    verdict = Verdict(
        verdict=result.verdict,
        score=float(result.judge_score) if result.judge_score is not None else 0.0,
        comment=result.judge_comment,
    )
    diag = await diagnosis_client.diagnose(trace, question.ground_truth_reasoning, verdict)

    existing = await session.scalar(
        select(SpanAnalysis).where(SpanAnalysis.question_result_id == result_id)
    )
    if existing is None:
        existing = SpanAnalysis(question_result_id=result_id)
        session.add(existing)
    existing.overall_diagnosis = diag["overall_diagnosis"]
    existing.caveat = diag.get("caveat")
    existing.raw_llm_output = diag
    existing.model_used = diagnosis_client.MODEL_NAME
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
