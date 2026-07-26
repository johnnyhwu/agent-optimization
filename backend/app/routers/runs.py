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
from app.schemas import RunOut
from app.sse import hub

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


@router.post("", response_model=RunOut, status_code=201)
async def trigger_run(
    eval_set_id: uuid.UUID,
    subject: str = Depends(require_reader),  # owner OR viewer may run (§6.16)
    session: AsyncSession = Depends(get_session),
):
    es = await session.get(EvalSet, eval_set_id)
    if es is None:
        raise HTTPException(status_code=404, detail="eval set not found")

    run = Run(eval_set_id=eval_set_id, triggered_by=subject, status="running")
    session.add(run)
    await session.commit()
    await session.refresh(run)

    task = asyncio.create_task(run_eval(run.id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return RunOut(
        id=run.id, eval_set_id=run.eval_set_id, triggered_by=run.triggered_by,
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
