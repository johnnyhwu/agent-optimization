"""Run endpoints: trigger a run, list runs, live SSE progress (§6.13 / §6.15)."""
from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.auth import require_reader
from app.db import get_session
from app.models import EvalSet, QuestionResult, Run
from app.orchestrator import run_eval
from app.schemas import RunConfig, RunCreate, RunOut
from app.sse import hub

# A credential and the endpoint it authenticates against, for the reuse rule
# below: {secret key in runs.secrets: endpoint key in runs.config}.
_SECRET_ENDPOINTS = {
    "llm_api_key": "llm_base_url",
    "langfuse_secret_key": "langfuse_host",
}

router = APIRouter(prefix="/eval-sets/{eval_set_id}/runs", tags=["runs"])

# Keep strong references so background tasks aren't garbage-collected.
_background_tasks: set[asyncio.Task] = set()


async def _incorrect_count(session: AsyncSession, run_id: uuid.UUID) -> int:
    return (
        await session.scalar(
            select(func.count())
            .select_from(QuestionResult)
            .where(QuestionResult.run_id == run_id, QuestionResult.verdict == "incorrect")
        )
    ) or 0


async def _resolve_secrets(
    session: AsyncSession,
    eval_set_id: uuid.UUID,
    body: RunCreate,
    config: dict,
) -> dict:
    """The credentials this run executes with: what was typed, plus anything
    borrowed from an earlier run.

    A borrowed credential is only carried over when the endpoint it authenticates
    against is unchanged. Without that rule a user could reuse a stored key while
    pointing the base URL at a server they control, and the backend would happily
    send someone else's credential there.
    """
    secrets = {k: v for k, v in body.secrets.model_dump().items() if v}
    if body.reuse_secrets_from_run_id is None:
        return secrets

    source = await session.get(Run, body.reuse_secrets_from_run_id)
    # Same eval set only — the caller has already been authorized for this one.
    if source is None or source.eval_set_id != eval_set_id:
        raise HTTPException(
            status_code=404, detail="run to reuse config from not found in this eval set"
        )

    source_config = source.config or {}
    for secret_key, endpoint_key in _SECRET_ENDPOINTS.items():
        if secrets.get(secret_key):
            continue  # explicitly typed in this request; nothing to borrow
        stored = (source.secrets or {}).get(secret_key)
        if not stored:
            continue
        if (config.get(endpoint_key) or "") != (source_config.get(endpoint_key) or ""):
            continue  # endpoint changed — the user must re-enter this credential
        secrets[secret_key] = stored
    return secrets


@router.post("", response_model=RunOut, status_code=201)
async def trigger_run(
    eval_set_id: uuid.UUID,
    body: RunCreate | None = None,
    subject: str = Depends(require_reader),  # owner OR viewer may run (§6.16)
    session: AsyncSession = Depends(get_session),
):
    es = await session.get(EvalSet, eval_set_id)
    if es is None:
        raise HTTPException(status_code=404, detail="eval set not found")

    body = body or RunCreate()
    # Blank fields are dropped rather than stored as "": the seams treat a missing
    # key as "fall back to the environment", and an empty string would mean the
    # same thing while making the stored config harder to read.
    config = {k: v for k, v in body.config.model_dump().items() if v not in ("", None)}
    secrets = await _resolve_secrets(session, eval_set_id, body, config)

    run = Run(
        eval_set_id=eval_set_id, triggered_by=subject, status="running",
        name=(body.name or "").strip() or None, config=config, secrets=secrets,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)

    task = asyncio.create_task(run_eval(run.id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return RunOut(
        id=run.id, eval_set_id=run.eval_set_id, triggered_by=run.triggered_by,
        name=run.name, config=RunConfig(**(run.config or {})),
        status=run.status, started_at=run.started_at, completed_at=run.completed_at,
        pass_rate=run.pass_rate, total_count=run.total_count,
        correct_count=run.correct_count, incorrect_count=0,
    )


@router.get("", response_model=list[RunOut])
async def list_runs(
    eval_set_id: uuid.UUID,
    subject: str = Depends(require_reader),
    session: AsyncSession = Depends(get_session),
):
    runs = (
        await session.scalars(
            select(Run).where(Run.eval_set_id == eval_set_id).order_by(Run.started_at.desc())
        )
    ).all()
    out = []
    for r in runs:
        out.append(
            RunOut(
                id=r.id, eval_set_id=r.eval_set_id, triggered_by=r.triggered_by,
                name=r.name, config=RunConfig(**(r.config or {})),
                status=r.status, started_at=r.started_at, completed_at=r.completed_at,
                pass_rate=float(r.pass_rate) if r.pass_rate is not None else None,
                total_count=r.total_count, correct_count=r.correct_count,
                incorrect_count=await _incorrect_count(session, r.id),
            )
        )
    return out


@router.get("/{run_id}/progress")
async def run_progress(
    eval_set_id: uuid.UUID,
    run_id: uuid.UUID,
    request: Request,
    subject: str = Depends(require_reader),
    session: AsyncSession = Depends(get_session),
):
    """SSE stream of live run progress (§6.15). Emits an initial snapshot, then
    live per-question events until the run completes."""
    run = await session.get(Run, run_id)
    if run is None or run.eval_set_id != eval_set_id:
        raise HTTPException(status_code=404, detail="run not found")

    queue = hub.subscribe(run_id)

    async def event_gen():
        try:
            # Initial snapshot so a late subscriber sees current state.
            done = (
                await session.scalar(
                    select(func.count()).select_from(QuestionResult)
                    .where(QuestionResult.run_id == run_id,
                           QuestionResult.status.in_(("done", "failed")))
                )
            ) or 0
            total = (
                await session.scalar(
                    select(func.count()).select_from(QuestionResult)
                    .where(QuestionResult.run_id == run_id)
                )
            ) or 0
            yield {"event": "snapshot",
                   "data": json.dumps({"status": run.status, "done": done, "total": total})}
            if run.status != "running":
                yield {"event": "run_completed",
                       "data": json.dumps({"status": run.status})}
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
                if event.get("type") == "run_completed":
                    break
        finally:
            hub.unsubscribe(run_id, queue)

    return EventSourceResponse(event_gen())
