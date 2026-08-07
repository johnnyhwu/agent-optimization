"""Results endpoints (§6.13 bottom tier).

- GET .../results   : left column — question list across one or more selected
  runs, with is_incorrect computed per the requested mode (union/intersection/
  last_n).
- GET .../results/{result_id}/trace : middle+right columns — live-fetched trace
  (full bodies; §6.7 truncation applies only before the diagnosis LLM) + stored
  diagnosis (read from DB, §6.12).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_reader
from app.db import get_session
from app.integrations import build_seams
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
from app.services.trace_view import resolve_trace_spans, span_to_out

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
    run_labels = {
        r.id: (r.name or r.started_at.isoformat(timespec="seconds"))
        for r in newest_first
    }
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
                id=rep.id, run_id=rep.run_id,
                run_label=run_labels.get(rep.run_id), question_pk=qpk,
                question_id=q.question_id, question=q.question,
                correlation_id=rep.correlation_id,
                agent_response=rep.agent_response, verdict=rep.verdict,
                judge_score=float(rep.judge_score) if rep.judge_score is not None else None,
                judge_comment=rep.judge_comment, status=rep.status,
                phase=result_phase(
                    rep.status, rep.agent_response, rep.verdict, rep.failure_kind
                ),
                error_message=rep.error_message, failure_kind=rep.failure_kind,
                agent_latency_ms=rep.agent_latency_ms,
                trace_ready=rep.trace_ready, has_analysis=rep.id in analyses_qr,
                is_incorrect=qpk in incorrect_set,
            )
        )
    # stable order by question_id
    out.sort(key=lambda r: r.question_id)
    return out


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
    # since this change, from "the trace store rejected us" and from "this
    # question hasn't run yet".
    spans: list[SpanOut] = []
    trace_error: str | None = None
    phase = result_phase(
        result.status, result.agent_response, result.verdict, result.failure_kind
    )
    traceable = result.status not in ("failed", "cancelled") and phase != "pending"

    # --- Every database read happens above this line ------------------------
    # `question` used to be read at the very end, after the trace fetch; it is an
    # unconditional read either way, so hoisting it changes nothing except that
    # the session now has nothing left to do. The run is still read only when a
    # trace is actually going to be fetched, exactly as before.
    #
    # The trace lives wherever the run that produced it was pointed, which is not
    # necessarily where the environment points today.
    question = await session.get(Question, result.question_pk)
    run = await session.get(Run, result.run_id) if traceable else None

    # Hand the connection back before touching Langfuse. `resolve_trace_spans`
    # polls up to `trace_poll_max_attempts` times, and each attempt can wait
    # `langfuse_timeout_s` (60s by default) per read strategy — holding a pooled
    # connection for all of that meant a slow trace store took the whole backend
    # down with it, not just the trace view (see app/db.py).
    #
    # `commit`, never `rollback`: rollback expires every loaded object, so the
    # `result` and `question` attributes read below would each become a lazy load
    # and raise MissingGreenlet. `expire_on_commit=False` is what makes this safe.
    await session.commit()

    if not traceable:
        if result.status in ("failed", "cancelled"):
            # The agent never answered (or was stopped), so there is nothing to
            # fetch.
            state = "no_trace"
        else:
            # The agent hasn't been asked yet, so no trace can exist for this
            # correlation_id. Calling the trace store here was worse than
            # useless: on a broken or misconfigured Langfuse it produced a fresh
            # error identical to the previous run's, which reads exactly like a
            # stale one being replayed. It also fired up to
            # trace_poll_max_attempts requests inside this request, per click,
            # for a question that hasn't started.
            state = "not_started"
    else:
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
            trace, fetch_error, fatal = await resolve_trace_spans(
                result.correlation_id, seams.trace
            )
            if trace is not None:
                state = "ready"
                # Full bodies, structured where the trace store had structure —
                # see services/trace_view.span_to_out for why the view path does
                # not truncate.
                spans = [span_to_out(s) for s in trace.spans]
            elif fetch_error is not None and fatal:
                state = "error"
                trace_error = fetch_error
            elif fetch_error is not None:
                # One Langfuse read path is broken, but another one says the
                # trace is merely still being ingested. Show the failure — it is
                # a real deployment fault — without declaring the trace lost.
                state = "generating"
                trace_error = fetch_error
            else:
                # Genuinely not ingested yet. Still surface whatever the run hit,
                # so "waiting for ingestion" doesn't hide a 401 from an hour ago.
                state = "generating"
                trace_error = result.trace_error

    return TraceView(
        trace_state=state, trace_error=trace_error,
        diagnosis_error=result.diagnosis_error,
        spans=spans, analysis=analysis,
        verdict=result.verdict, judge_comment=result.judge_comment,
        agent_response=result.agent_response,
        ground_truth_response=question.ground_truth_response if question else None,
        ground_truth_reasoning=question.ground_truth_reasoning if question else None,
        error_message=result.error_message,
    )
