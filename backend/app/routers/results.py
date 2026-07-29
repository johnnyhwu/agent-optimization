"""Results endpoints (§6.13 bottom tier).

- GET .../results   : left column — question list across one or more selected
  runs, with is_incorrect computed per the requested mode (union/intersection/
  last_n).
- GET .../results/{result_id}/trace : middle+right columns — live-fetched trace
  (truncated per §6.7) + stored diagnosis (read from DB, §6.12).
"""
from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_reader
from app.config import settings
from app.db import get_session
from app.integrations import build_seams
from app.integrations.base import NotReady
from app.models import Question, QuestionResult, Run, SpanAnalysis
from app.routers._helpers import load_run_verdicts
from app.schemas import (
    AnalysisOut,
    QuestionResultOut,
    SpanOut,
    SuspectOut,
    TraceView,
)
from app.services.aggregation import incorrect_by_mode, result_phase
from app.services.truncation import truncate_body

router = APIRouter(prefix="/eval-sets/{eval_set_id}", tags=["results"])


@router.get("/results", response_model=list[QuestionResultOut])
async def list_results(
    eval_set_id: uuid.UUID,
    run_ids: list[uuid.UUID] = Query(...),
    mode: str = Query("union", pattern="^(union|intersection|last_n)$"),
    last_n: int = Query(2, ge=1),
    subject: str = Depends(require_reader),
    session: AsyncSession = Depends(get_session),
):
    runs = (
        await session.scalars(
            select(Run)
            .where(Run.eval_set_id == eval_set_id, Run.id.in_(run_ids))
            .order_by(Run.started_at.desc())
        )
    ).all()
    if not runs:
        raise HTTPException(status_code=404, detail="no matching runs in this set")

    newest_first = list(runs)
    verdicts = await load_run_verdicts(session, newest_first)
    incorrect_set = incorrect_by_mode(verdicts, mode, last_n=last_n)

    # All results across selected runs, plus question text.
    run_ids_present = [r.id for r in newest_first]
    results = (
        await session.scalars(
            select(QuestionResult).where(QuestionResult.run_id.in_(run_ids_present))
        )
    ).all()
    questions = {
        q.id: q
        for q in (
            await session.scalars(
                select(Question).where(Question.eval_set_id == eval_set_id)
            )
        ).all()
    }
    result_ids = [r.id for r in results]
    analyses_qr: set[uuid.UUID] = set()
    if result_ids:
        analyses_qr = set(
            (
                await session.scalars(
                    select(SpanAnalysis.question_result_id).where(
                        SpanAnalysis.question_result_id.in_(result_ids)
                    )
                )
            ).all()
        )

    # index results by question_pk, newest run first
    run_order = {r.id: i for i, r in enumerate(newest_first)}
    by_q: dict[uuid.UUID, list[QuestionResult]] = {}
    for r in results:
        by_q.setdefault(r.question_pk, []).append(r)
    for lst in by_q.values():
        lst.sort(key=lambda r: run_order.get(r.run_id, 1_000))

    out: list[QuestionResultOut] = []
    for qpk, q in questions.items():
        reps = by_q.get(qpk)
        if not reps:
            continue
        # Representative result to open: prefer an incorrect one with a diagnosis,
        # else the newest.
        rep = next(
            (r for r in reps if r.verdict == "incorrect" and r.id in analyses_qr),
            reps[0],
        )
        out.append(
            QuestionResultOut(
                id=rep.id, run_id=rep.run_id, question_pk=qpk,
                question_id=q.question_id, question=q.question,
                correlation_id=rep.correlation_id,
                agent_response=rep.agent_response, verdict=rep.verdict,
                judge_score=float(rep.judge_score) if rep.judge_score is not None else None,
                judge_comment=rep.judge_comment, status=rep.status,
                phase=result_phase(rep.status, rep.agent_response, rep.verdict),
                error_message=rep.error_message,
                agent_latency_ms=rep.agent_latency_ms,
                trace_ready=rep.trace_ready, has_analysis=rep.id in analyses_qr,
                is_incorrect=qpk in incorrect_set,
            )
        )
    # stable order by question_id
    out.sort(key=lambda r: r.question_id)
    return out


async def _resolve_trace_spans(correlation_id: str, trace_client):
    """Light poll of the trace store for the view path.

    Returns (trace_or_None, error_or_None). The error is returned rather than
    swallowed: an unreachable Langfuse, a rejected key and a trace that is still
    being ingested all produce "no spans", and showing the same "still
    generating" message for all three is indistinguishable from the platform
    being broken.

    Short sleeps on purpose: this runs inside a request, so it must not block for
    the orchestrator's much longer ingestion backoff. If the trace still isn't
    there the view falls back to the "generating" state and the user retries."""
    for _ in range(settings.trace_poll_max_attempts):
        try:
            trace = await trace_client.fetch_trace(correlation_id)
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            return None, f"{type(exc).__name__}: {exc}"
        if not isinstance(trace, NotReady):
            return trace, None
        await asyncio.sleep(0.05)
    return None, None


@router.get("/results/{result_id}/trace", response_model=TraceView)
async def get_trace(
    eval_set_id: uuid.UUID,
    result_id: uuid.UUID,
    subject: str = Depends(require_reader),
    session: AsyncSession = Depends(get_session),
):
    result = await session.get(QuestionResult, result_id)
    if result is None:
        raise HTTPException(status_code=404, detail="result not found")

    analysis_row = await session.scalar(
        select(SpanAnalysis).where(SpanAnalysis.question_result_id == result_id)
    )
    analysis = None
    if analysis_row is not None:
        raw = analysis_row.raw_llm_output or {}
        analysis = AnalysisOut(
            overall_diagnosis=analysis_row.overall_diagnosis,
            caveat=analysis_row.caveat,
            suspects=[SuspectOut(**s) for s in raw.get("suspects", [])],
            generated_at=analysis_row.generated_at,
            model_used=analysis_row.model_used,
        )

    # §6.12 / §7.1 #5: distinguish "generating (retrying)" from "no trace" — and,
    # since this change, from "the trace store rejected us".
    spans: list[SpanOut] = []
    trace_error: str | None = None
    if result.status in ("failed", "cancelled"):
        # The agent never answered (or was stopped), so there is nothing to fetch.
        state = "no_trace"
    else:
        # The trace lives wherever the run that produced it was pointed, which is
        # not necessarily where the environment points today.
        run = await session.get(Run, result.run_id)
        try:
            seams = build_seams(
                run.config if run else None, run.secrets if run else None
            )
        except Exception as exc:  # noqa: BLE001
            # TRACE_IMPL=real with no host or an incomplete key pair raises here.
            # Previously a 500; the developer needs to read the reason, not a
            # stack trace in the backend log.
            state = "error"
            trace_error = f"{type(exc).__name__}: {exc}"
        else:
            # Fetched even when trace_ready is false: that flag only records what
            # the orchestrator managed at run time, and never retrying means a
            # misconfigured trace store shows "generating" forever.
            trace, fetch_error = await _resolve_trace_spans(
                result.correlation_id, seams.trace
            )
            if trace is not None:
                state = "ready"
                for s in trace.spans:
                    in_body, in_trunc = truncate_body(s.input)
                    out_body, out_trunc = truncate_body(s.output)
                    spans.append(
                        SpanOut(
                            index=s.index, tool_name=s.tool_name, status=s.status,
                            input=in_body, output=out_body, token_usage=s.token_usage,
                            input_truncated=in_trunc, output_truncated=out_trunc,
                            status_message=s.status_message,
                        )
                    )
            elif fetch_error is not None:
                state = "error"
                trace_error = fetch_error
            else:
                # Genuinely not ingested yet. Still surface whatever the run hit,
                # so "waiting for ingestion" doesn't hide a 401 from an hour ago.
                state = "generating"
                trace_error = result.trace_error

    question = await session.get(Question, result.question_pk)

    return TraceView(
        trace_state=state, trace_error=trace_error,
        diagnosis_error=result.diagnosis_error,
        spans=spans, analysis=analysis,
        verdict=result.verdict, judge_comment=result.judge_comment,
        agent_response=result.agent_response,
        ground_truth_response=question.ground_truth_response if question else None,
        error_message=result.error_message,
    )
