"""Run endpoints: trigger, list, live SSE progress, cancel, delete
(§6.13 / §6.15 / §9.14)."""
from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app import cancellation
from app.auth import require_owner, require_reader, role_for
from app.db import get_session
from app.models import EvalSet, QuestionResult, Run
from app.orchestrator import run_eval
from app.schemas import RunConfig, RunCreate, RunOut, RunPage
from app.services import run_config
from app.services.deletion import delete_run as delete_run_rows
from app.sse import hub

# A credential and the endpoint it authenticates against, for the reuse rule
# below: {secret key in runs.secrets: endpoint key in runs.config}.
_SECRET_ENDPOINTS = {
    "llm_api_key": "llm_base_url",
    "langfuse_secret_key": "langfuse_host",
}

# The same credentials as UI-facing slot names. Only these names are ever sent
# outward — the values behind them stay in the database.
_SECRET_SLOTS = {"llm_api_key": "llm", "langfuse_secret_key": "langfuse"}

router = APIRouter(prefix="/eval-sets/{eval_set_id}/runs", tags=["runs"])

# Keep strong references so background tasks aren't garbage-collected.
_background_tasks: set[asyncio.Task] = set()


def _credentials_set(run: Run) -> list[str]:
    """Which credential slots this run recorded. Names only, never values."""
    stored = run.secrets or {}
    return [slot for key, slot in _SECRET_SLOTS.items() if stored.get(key)]


async def _incorrect_count(session: AsyncSession, run_id: uuid.UUID) -> int:
    return (
        await session.scalar(
            select(func.count())
            .select_from(QuestionResult)
            .where(QuestionResult.run_id == run_id, QuestionResult.verdict == "incorrect")
        )
    ) or 0


async def _incorrect_counts(
    session: AsyncSession, run_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """Incorrect counts for a page of runs in one query.

    The per-run version above is fine for a single run; issuing it in a loop made
    listing N runs cost N round trips, which is the kind of thing that only shows
    up once someone has a few hundred runs of history.
    """
    if not run_ids:
        return {}
    rows = (
        await session.execute(
            select(QuestionResult.run_id, func.count())
            .where(
                QuestionResult.run_id.in_(run_ids),
                QuestionResult.verdict == "incorrect",
            )
            .group_by(QuestionResult.run_id)
        )
    ).all()
    return {run_id: n for run_id, n in rows}


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
    # Materialize: a field left blank is stored with the environment's value, not
    # dropped, so the run's config reads as a complete record of what it used
    # rather than a set of deltas against an environment that may since have
    # changed. Must precede _resolve_secrets, which compares endpoints against
    # the source run's — both sides are then fully populated.
    config = run_config.resolve(body.config)
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

    return _run_out(run, incorrect_count=0)


def _run_out(run: Run, incorrect_count: int) -> RunOut:
    return RunOut(
        id=run.id, eval_set_id=run.eval_set_id, triggered_by=run.triggered_by,
        name=run.name, config=RunConfig(**(run.config or {})),
        credentials_set=_credentials_set(run),
        status=run.status, cancel_requested=bool(run.cancel_requested),
        started_at=run.started_at, completed_at=run.completed_at,
        pass_rate=float(run.pass_rate) if run.pass_rate is not None else None,
        total_count=run.total_count, correct_count=run.correct_count,
        incorrect_count=incorrect_count,
    )


@router.get("", response_model=RunPage)
async def list_runs(
    eval_set_id: uuid.UUID,
    q: str | None = Query(None, description="case-insensitive run name substring"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    subject: str = Depends(require_reader),
    session: AsyncSession = Depends(get_session),
):
    """A page of runs, newest first.

    Paged because a long-lived eval set accumulates runs indefinitely, and both
    the history list and the "use config from" picker read this endpoint.
    """
    base = select(Run).where(Run.eval_set_id == eval_set_id)
    if q:
        base = base.where(Run.name.ilike(f"%{q}%"))

    total = await session.scalar(select(func.count()).select_from(base.subquery())) or 0
    # id as a tiebreaker so runs started in the same instant can't shuffle
    # between pages and appear twice (or not at all).
    runs = (
        await session.scalars(
            base.order_by(Run.started_at.desc(), Run.id.asc()).limit(limit).offset(offset)
        )
    ).all()

    counts = await _incorrect_counts(session, [r.id for r in runs])
    items = [_run_out(r, counts.get(r.id, 0)) for r in runs]
    return RunPage(items=items, total=total, has_more=offset + len(items) < total)


@router.get("/{run_id}", response_model=RunOut)
async def get_run(
    eval_set_id: uuid.UUID,
    run_id: uuid.UUID,
    subject: str = Depends(require_reader),
    session: AsyncSession = Depends(get_session),
):
    """One run. The detail view needs a specific run's status and triggered_by to
    decide whether to offer the stop button; before this it read the whole run
    list and searched it, which stopped working the moment that list was paged."""
    run = await session.get(Run, run_id)
    if run is None or run.eval_set_id != eval_set_id:
        raise HTTPException(status_code=404, detail="run not found")
    return _run_out(run, await _incorrect_count(session, run_id))


@router.post("/{run_id}/cancel", response_model=RunOut)
async def cancel_run(
    eval_set_id: uuid.UUID,
    run_id: uuid.UUID,
    subject: str = Depends(require_reader),
    session: AsyncSession = Depends(get_session),
):
    """Stop a running eval (§9.14).

    Wider than the owner-only write rule on purpose: a viewer may trigger a run
    (§6.16), and someone who can start a run against a real agent must be able to
    stop it. Owners can stop anyone's.
    """
    run = await session.get(Run, run_id)
    if run is None or run.eval_set_id != eval_set_id:
        raise HTTPException(status_code=404, detail="run not found")
    if run.status != "running":
        raise HTTPException(
            status_code=409, detail=f"run is already {run.status}; nothing to cancel"
        )
    if run.triggered_by != subject:
        role = await role_for(session, eval_set_id, subject)
        if role != "owner":
            raise HTTPException(
                status_code=403,
                detail="only an owner or the person who started this run can cancel it",
            )

    run.cancel_requested = True
    await session.commit()
    # The DB flag is the durable record; the event is what actually interrupts
    # the in-flight agent call (see app/cancellation.py).
    cancellation.signal(run_id)
    return _run_out(run, await _incorrect_count(session, run_id))


@router.delete("/{run_id}", status_code=204)
async def delete_run(
    eval_set_id: uuid.UUID,
    run_id: uuid.UUID,
    subject: str = Depends(require_owner),  # §6.16: deleting a run is owner-only
    session: AsyncSession = Depends(get_session),
):
    run = await session.get(Run, run_id)
    if run is None or run.eval_set_id != eval_set_id:
        raise HTTPException(status_code=404, detail="run not found")
    if run.status == "running":
        # The orchestrator is still writing to these rows. Cancel first — that is
        # what the stop button is for.
        raise HTTPException(
            status_code=409, detail="cancel the run before deleting it"
        )
    await delete_run_rows(session, run_id)
    await session.commit()
    return Response(status_code=204)


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
            correct = (
                await session.scalar(
                    select(func.count()).select_from(QuestionResult)
                    .where(QuestionResult.run_id == run_id,
                           QuestionResult.verdict == "correct")
                )
            ) or 0
            yield {"event": "snapshot",
                   "data": json.dumps({"status": run.status, "done": done,
                                       "total": total, "correct": correct})}
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
