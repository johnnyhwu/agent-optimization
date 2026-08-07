"""Downloading an eval set as files (§6.13 card action).

Two endpoints, and the split is the whole point of the feature's design: the
dialog shows a *preview* of the exact files it is about to produce — their
names, their columns, their real row counts — so the developer never has to
press Download to find out what they get. `/export/preview` is what makes those
counts real numbers instead of guesses.

Both are behind `require_reader`, not `require_owner`: a viewer can already read
every one of these rows on screen, so refusing them the CSV would protect
nothing while making the feature useless to exactly the people who mostly want
it (§11.4).
"""
from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import require_reader
from app.config import settings
from app.db import get_session
from app.integrations import build_seams
from app.models import EvalSet, Question, QuestionResult, Run, SpanAnalysis
from app.services import export as export_service
from app.services.aggregation import result_phase
from app.services.trace_view import resolve_trace_spans, span_to_out

router = APIRouter(prefix="/eval-sets/{eval_set_id}", tags=["export"])

# Which credential *slots* a run recorded — names only, never values. Duplicated
# from routers/runs.py rather than imported so that neither module has to import
# the other; both are three lines over the same two keys.
_SECRET_SLOTS = {"langfuse_secret_key": "langfuse", "llm_api_key": "llm"}


async def _load_set(session: AsyncSession, eval_set_id: uuid.UUID) -> EvalSet:
    eval_set = await session.get(EvalSet, eval_set_id)
    if eval_set is None:
        raise HTTPException(status_code=404, detail="eval set not found")
    return eval_set


async def _load_questions(session: AsyncSession, eval_set_id: uuid.UUID) -> list[Question]:
    return list(
        (
            await session.scalars(
                select(Question)
                .where(Question.eval_set_id == eval_set_id)
                .options(selectinload(Question.skills))
                .order_by(Question.question_id.asc())
            )
        ).all()
    )


async def _select_runs(
    session: AsyncSession,
    eval_set_id: uuid.UUID,
    scope: str,
    last_n: int,
    run_ids: list[uuid.UUID] | None,
) -> list[Run]:
    """The runs this export covers, newest first.

    `selected` is filtered against this eval set rather than trusted: a run id
    from another set would otherwise export that set's results under this set's
    provenance columns, which is precisely the mislabelling those columns exist
    to prevent.
    """
    stmt = select(Run).where(Run.eval_set_id == eval_set_id)
    if scope == "selected":
        if not run_ids:
            return []
        stmt = stmt.where(Run.id.in_(run_ids))
    stmt = stmt.order_by(Run.started_at.desc(), Run.id.asc())
    if scope == "latest":
        stmt = stmt.limit(1)
    elif scope == "latest_n":
        stmt = stmt.limit(last_n)
    return list((await session.scalars(stmt)).all())


async def _load_results(
    session: AsyncSession, runs: list[Run]
) -> list[QuestionResult]:
    if not runs:
        return []
    return list(
        (
            await session.scalars(
                select(QuestionResult).where(
                    QuestionResult.run_id.in_([r.id for r in runs])
                )
            )
        ).all()
    )


def _credentials_by_run(runs: list[Run]) -> dict[uuid.UUID, list[str]]:
    return {
        run.id: [
            slot for key, slot in _SECRET_SLOTS.items() if (run.secrets or {}).get(key)
        ]
        for run in runs
    }


def _traceable(result: QuestionResult) -> bool:
    """Whether this result could have a trace worth fetching.

    Same rule as `GET .../trace`: a failed or cancelled question never produced
    one, and a pending question has not been asked yet — reaching for either
    means N pointless round trips against the trace store per export.
    """
    if result.status in ("failed", "cancelled"):
        return False
    return result_phase(result.status, result.agent_response, result.verdict) != "pending"


async def _collect_traces(
    session: AsyncSession,
    runs: list[Run],
    results: list[QuestionResult],
    questions_by_pk: dict[uuid.UUID, Question],
) -> tuple[list[dict], bool]:
    """Traces and diagnoses for the selected results.

    Returns (entries, truncated). The diagnosis half is a database read and is
    always present; the span half is a live read against wherever *that run* was
    pointed, which is why the seams are built per run rather than once per
    process — an old run may have logged to a different Langfuse than the
    environment names today.

    Every entry carries a `trace_state`, including the failures. An export that
    silently omitted the traces it could not fetch would read as "this run had
    no traces", which is a different and much more alarming claim than
    "ingestion had not landed for 12 of them".
    """
    runs_by_id = {run.id: run for run in runs}
    analyses = {}
    result_ids = [r.id for r in results]
    if result_ids:
        rows = (
            await session.scalars(
                select(SpanAnalysis).where(
                    SpanAnalysis.question_result_id.in_(result_ids)
                )
            )
        ).all()
        analyses = {row.question_result_id: row for row in rows}

    # Newest run first, question order stable within a run — same ordering as
    # results.*, so the two files can be read side by side.
    ordered: list[tuple[Run, QuestionResult, Question]] = []
    by_run: dict[uuid.UUID, list[QuestionResult]] = {}
    for r in results:
        by_run.setdefault(r.run_id, []).append(r)
    for run in runs:
        pairs = [
            (questions_by_pk[r.question_pk], r)
            for r in by_run.get(run.id, [])
            if r.question_pk in questions_by_pk
        ]
        pairs.sort(key=lambda pair: pair[0].question_id)
        ordered.extend((run, r, q) for q, r in pairs)

    truncated = len(ordered) > settings.export_max_traces
    ordered = ordered[: settings.export_max_traces]

    # One seam set per run, built once. `build_seams` raises when TRACE_IMPL is
    # real but the run recorded no host or an incomplete key pair; that is a
    # per-run condition, so it is recorded once against every entry of that run
    # instead of being retried per question.
    seams_by_run: dict[uuid.UUID, object] = {}
    seam_errors: dict[uuid.UUID, str] = {}
    for run in runs_by_id.values():
        try:
            seams_by_run[run.id] = build_seams(run.config, run.secrets)
        except Exception as exc:  # noqa: BLE001 - reported per entry, not raised
            seam_errors[run.id] = f"{type(exc).__name__}: {exc}"

    # Every database read this export needs is done by now; the gather below is
    # pure outbound traffic. Handing the connection back first matters here more
    # than anywhere else: `export_max_traces` is 1000 and the concurrency is 8,
    # so a trace-carrying export can occupy one pooled connection for minutes
    # while doing nothing with it (see app/db.py).
    #
    # `commit`, never `rollback`: the rows loaded above (`analyses`, and the runs
    # and results the caller passed in) are read throughout `one`, and rollback
    # would expire them into lazy loads that raise MissingGreenlet.
    await session.commit()

    semaphore = asyncio.Semaphore(max(1, settings.export_trace_concurrency))

    async def one(run: Run, result: QuestionResult, question: Question) -> dict:
        entry = {
            "eval_set_id": str(run.eval_set_id),
            "run_id": str(run.id),
            "run_name": export_service.run_label(run),
            "question_id": question.question_id,
            "question": question.question,
            "correlation_id": result.correlation_id,
            "verdict": result.verdict,
            "judge_comment": result.judge_comment,
            "diagnosis_error": result.diagnosis_error,
            "trace_state": "no_trace",
            "trace_error": None,
            "spans": [],
            "analysis": None,
        }

        analysis_row = analyses.get(result.id)
        if analysis_row is not None:
            raw = analysis_row.raw_llm_output or {}
            entry["analysis"] = {
                "overall_diagnosis": analysis_row.overall_diagnosis,
                "caveat": analysis_row.caveat,
                "suspects": raw.get("suspects", []),
                "generated_at": analysis_row.generated_at.isoformat(),
                "model_used": analysis_row.model_used,
            }

        if not _traceable(result):
            entry["trace_state"] = (
                "no_trace" if result.status in ("failed", "cancelled") else "not_started"
            )
            return entry
        if run.id in seam_errors:
            entry["trace_state"] = "error"
            entry["trace_error"] = seam_errors[run.id]
            return entry

        async with semaphore:
            trace, fetch_error, fatal = await resolve_trace_spans(
                result.correlation_id, seams_by_run[run.id].trace
            )
        if trace is not None:
            entry["trace_state"] = "ready"
            entry["spans"] = [span_to_out(s).model_dump() for s in trace.spans]
        elif fetch_error is not None:
            # A partial failure still means "no spans here", but the export says
            # generating rather than error so a slow ingestion doesn't read as a
            # dead trace in the downloaded bundle.
            entry["trace_state"] = "error" if fatal else "generating"
            entry["trace_error"] = fetch_error
        else:
            entry["trace_state"] = "generating"
            entry["trace_error"] = result.trace_error
        return entry

    entries = await asyncio.gather(*(one(run, r, q) for run, r, q in ordered))
    return list(entries), truncated


@router.get("/export/preview")
async def export_preview(
    eval_set_id: uuid.UUID,
    run_scope: str = Query("latest_n", pattern="^(all|latest|latest_n|selected)$"),
    last_n: int = Query(5, ge=1, le=100),
    run_ids: list[uuid.UUID] = Query(default_factory=list),
    subject: str = Depends(require_reader),
    session: AsyncSession = Depends(get_session),
):
    """The numbers the download dialog puts next to each filename.

    Counted rather than estimated, including the awkward ones: how many of the
    selected results are still running, and how many traces the orchestrator
    actually managed to mark ready. A preview that quietly rounded those away
    would be the one thing that stops the panel being trusted.
    """
    eval_set = await _load_set(session, eval_set_id)
    questions = await _load_questions(session, eval_set_id)
    runs = await _select_runs(session, eval_set_id, run_scope, last_n, run_ids)
    results = await _load_results(session, runs)

    total_runs = len(
        (
            await session.scalars(select(Run.id).where(Run.eval_set_id == eval_set_id))
        ).all()
    )
    traceable = [r for r in results if _traceable(r)]
    return {
        "eval_set_id": str(eval_set.id),
        "eval_set_name": eval_set.name,
        "filename_stem": export_service.slugify(eval_set.name),
        # The dialog lists each file's columns so the developer can read the
        # exact header before downloading. Served from the same tuples the
        # writer uses rather than restated in the frontend — a hardcoded copy
        # would drift, and a panel that misnames a column is worse than no
        # panel, since its whole job is to be trusted.
        "columns": {
            "questions": list(export_service.QUESTION_FIELDS),
            "runs": list(export_service.RUN_FIELDS),
            "results": list(export_service.RESULT_FIELDS),
        },
        "questions": len(questions),
        "runs": len(runs),
        "total_runs": total_runs,
        "results": len(results),
        "results_running": sum(1 for r in results if r.status == "pending"),
        "traces": len(traceable),
        "traces_ready": sum(1 for r in traceable if r.trace_ready),
        "traces_capped": len(traceable) > settings.export_max_traces,
        "max_traces": settings.export_max_traces,
    }


@router.get("/export")
async def export_eval_set(
    eval_set_id: uuid.UUID,
    questions: bool = Query(True),
    runs: bool = Query(True),
    traces: bool = Query(False),
    fmt: str = Query("csv", pattern="^(csv|jsonl)$"),
    run_scope: str = Query("latest_n", pattern="^(all|latest|latest_n|selected)$"),
    last_n: int = Query(5, ge=1, le=100),
    run_ids: list[uuid.UUID] = Query(default_factory=list),
    subject: str = Depends(require_reader),
    session: AsyncSession = Depends(get_session),
):
    """The download itself.

    A single selected file is returned as that file, not as a one-entry ZIP —
    "will I get a file or an archive?" is its own small uncertainty, and the
    dialog answers it by naming the exact thing it is about to hand over.
    """
    if not (questions or runs or traces):
        raise HTTPException(status_code=422, detail="select at least one file to export")

    eval_set = await _load_set(session, eval_set_id)
    ext = "csv" if fmt == "csv" else "jsonl"
    serialize = export_service.to_csv if fmt == "csv" else export_service.to_jsonl

    # Loaded once: `questions.*` needs the rows, and results need the same rows
    # indexed by pk to resolve each result's question text.
    all_questions = await _load_questions(session, eval_set_id)

    files: dict[str, str | bytes] = {}
    counts: dict[str, int] = {}
    selected_runs: list[Run] = []

    if questions:
        rows = export_service.question_rows(eval_set, all_questions)
        files[f"questions.{ext}"] = serialize(rows, export_service.QUESTION_FIELDS)
        counts["questions"] = len(rows)

    if runs or traces:
        selected_runs = await _select_runs(
            session, eval_set_id, run_scope, last_n, run_ids
        )
        result_records = await _load_results(session, selected_runs)
        questions_by_pk = {q.id: q for q in all_questions}

        if runs:
            run_rows = export_service.run_rows(
                eval_set, selected_runs, _credentials_by_run(selected_runs)
            )
            files[f"runs.{ext}"] = serialize(run_rows, export_service.RUN_FIELDS)
            counts["runs"] = len(run_rows)

            result_rows = export_service.result_rows(
                eval_set, selected_runs, result_records, questions_by_pk
            )
            files[f"results.{ext}"] = serialize(
                result_rows, export_service.RESULT_FIELDS
            )
            counts["results"] = len(result_rows)

        if traces:
            entries, truncated = await _collect_traces(
                session, selected_runs, result_records, questions_by_pk
            )
            files["traces.json"] = export_service.json_document(
                {
                    "eval_set_id": str(eval_set.id),
                    "eval_set_name": eval_set.name,
                    "truncated": truncated,
                    "max_traces": settings.export_max_traces,
                    "traces": entries,
                }
            )
            counts["traces"] = len(entries)

    stem = export_service.slugify(eval_set.name)
    date = export_service.today_stamp()

    # One file selected -> hand over that file. Only a real bundle gets zipped.
    if len(files) == 1:
        name, content = next(iter(files.items()))
        base, _, suffix = name.rpartition(".")
        filename = f"{stem}-{base}-{date}.{suffix}"
        return Response(
            content=content.encode("utf-8") if isinstance(content, str) else content,
            media_type=export_service.MEDIA_TYPES.get(suffix, "application/octet-stream"),
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # Listing the data files only — a manifest that listed itself would be the
    # one entry a reader cannot use to find anything.
    manifest = export_service.build_manifest(
        eval_set,
        exported_by=subject,
        files=sorted(files),
        counts=counts,
        run_ids=[r.id for r in selected_runs],
        fmt=fmt,
    )
    files["manifest.json"] = export_service.json_document(manifest)
    filename = f"{stem}-{date}.zip"
    return Response(
        content=export_service.build_zip(files),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
