"""Optimize (Stage 3): the run list and one run's overview.

Mounted at `/optimization`, which shares no prefix with `/eval-sets` or
`/playground` — Optimize is a sibling section, not a fourth tier of Evaluation
(`docs/spec.md` §10.1), and the URL says so.

**Visibility is derived, not shared.** A run has no role table of its own. You
can see it if you can read *every* eval set it drew questions from, which is the
only rule that cannot leak: a run's item snapshots carry question text from
those sets, so being able to open the run is being able to read them. Deriving it
also means there is no second sharing UI to keep in step with the first, and no
way for the two to disagree.

The creator owns it — cancel, resume and delete are theirs. That matches how a
run already works (`§6.16`: a viewer may stop their own run) without inventing a
new role vocabulary for a second kind of run.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app import cancellation
from app.auth import current_subject
from app.db import SessionLocal, get_session
from app.models import (
    EvalSetRole,
    OptimizationItem,
    OptimizationRollout,
    OptimizationRun,
    OptimizationStep,
)
from app.optimizer import runner
from app.schemas import (
    OptimizationRunDetail,
    OptimizationRunOut,
    OptimizationRunPage,
    OptimizationStepSummary,
)
from app.sse import hub, resync_if_dropped, resync_or_ping

router = APIRouter(prefix="/optimization", tags=["optimization"])


async def _readable_run_ids(session: AsyncSession, subject: str):
    """A subquery of the run ids this subject may see.

    "Reader on every source eval set" expressed in SQL rather than in Python
    over a loaded page: filtering after the fact would make the page size depend
    on how many runs the caller happens to be locked out of, and "Showing 12 of
    40" would stop meaning anything.

    A run whose sources have all been deleted (`ON DELETE SET NULL` leaves a NULL
    behind) falls back to its creator — the questions it quoted are gone, so
    there is nothing left to leak, and the run is still their history.
    """
    readable = select(EvalSetRole.eval_set_id).where(EvalSetRole.user_subject == subject)
    # Runs with at least one source the caller cannot read.
    blocked = (
        select(OptimizationItem.run_id)
        .where(
            OptimizationItem.source_eval_set_id.is_not(None),
            OptimizationItem.source_eval_set_id.not_in(readable),
        )
        .distinct()
    )
    return select(OptimizationRun.id).where(
        OptimizationRun.id.not_in(blocked),
        # Nothing here is public: a run you have no relationship to at all is not
        # yours to see just because its sources happen to be readable.
        OptimizationRun.id.in_(
            select(OptimizationItem.run_id).where(
                OptimizationItem.source_eval_set_id.in_(readable)
            )
        )
        | (OptimizationRun.created_by == subject),
    )


async def _load_visible_run(
    session: AsyncSession, run_id: uuid.UUID, subject: str
) -> OptimizationRun:
    """One run the caller may see, or 404.

    404 rather than 403 for a run they may not see, the same choice the
    playground makes for another developer's attempt: whether a run exists at a
    given id is itself not theirs to learn.
    """
    run = await session.get(OptimizationRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="optimization run not found")
    visible = await session.scalar(
        select(func.count()).select_from(
            (await _readable_run_ids(session, subject)).where(
                OptimizationRun.id == run_id
            ).subquery()
        )
    )
    if not visible:
        raise HTTPException(status_code=404, detail="optimization run not found")
    return run


async def _counts(session: AsyncSession, run_ids: list[uuid.UUID]):
    """Per-run split sizes, source sets and finished-step counts, in three queries.

    Not one query per run: `docs/spec.md` §10.2③ records what N+1 did to
    `GET /eval-sets` (180 queries for one page), and a run list is exactly the
    same shape of surface.
    """
    if not run_ids:
        return {}, {}, {}

    split_rows = await session.execute(
        select(OptimizationItem.run_id, OptimizationItem.split, func.count())
        .where(OptimizationItem.run_id.in_(run_ids))
        .group_by(OptimizationItem.run_id, OptimizationItem.split)
    )
    splits: dict[uuid.UUID, dict[str, int]] = {}
    for run_id, split, count in split_rows:
        splits.setdefault(run_id, {})[split] = count

    source_rows = await session.execute(
        select(OptimizationItem.run_id, OptimizationItem.source_eval_set_id)
        .where(
            OptimizationItem.run_id.in_(run_ids),
            OptimizationItem.source_eval_set_id.is_not(None),
        )
        .distinct()
    )
    sources: dict[uuid.UUID, list[uuid.UUID]] = {}
    for run_id, eval_set_id in source_rows:
        sources.setdefault(run_id, []).append(eval_set_id)

    step_rows = await session.execute(
        select(OptimizationStep.run_id, func.count())
        .where(OptimizationStep.run_id.in_(run_ids), OptimizationStep.status == "done")
        .group_by(OptimizationStep.run_id)
    )
    steps = {run_id: count for run_id, count in step_rows}

    return splits, sources, steps


def _run_out(run: OptimizationRun, splits, sources, steps_done) -> OptimizationRunOut:
    return OptimizationRunOut(
        id=run.id,
        name=run.name,
        created_by=run.created_by,
        status=run.status,
        mode=run.mode,
        skill_name=run.skill_name,
        num_epochs=run.num_epochs,
        batch_size=run.batch_size,
        steps_per_epoch=run.steps_per_epoch,
        total_steps=run.total_steps,
        steps_done=steps_done,
        best_step=run.best_step,
        best_score=float(run.best_score) if run.best_score is not None else None,
        cancel_requested=run.cancel_requested,
        error_message=run.error_message,
        started_at=run.started_at,
        completed_at=run.completed_at,
        source_eval_set_ids=sorted(sources, key=str),
        n_train=splits.get("train", 0),
        n_val=splits.get("val", 0),
    )


@router.get("/runs", response_model=OptimizationRunPage)
async def list_optimization_runs(
    q: str | None = Query(None, description="case-insensitive name substring"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    subject: str = Depends(current_subject),
    session: AsyncSession = Depends(get_session),
):
    """A page of optimization runs, newest first."""
    visible = await _readable_run_ids(session, subject)
    base = select(OptimizationRun).where(OptimizationRun.id.in_(visible))
    if q:
        base = base.where(OptimizationRun.name.ilike(f"%{q}%"))

    total = await session.scalar(select(func.count()).select_from(base.subquery())) or 0
    # id as a tiebreaker, same reason as the run list: two runs started in the
    # same instant must not shuffle between pages.
    runs = (
        await session.scalars(
            base.order_by(OptimizationRun.started_at.desc(), OptimizationRun.id.asc())
            .limit(limit)
            .offset(offset)
        )
    ).all()

    splits, sources, steps = await _counts(session, [r.id for r in runs])
    items = [
        _run_out(r, splits.get(r.id, {}), sources.get(r.id, []), steps.get(r.id, 0))
        for r in runs
    ]
    return OptimizationRunPage(
        items=items, total=total, has_more=offset + len(items) < total
    )


@router.get("/runs/{run_id}", response_model=OptimizationRunDetail)
async def get_optimization_run(
    run_id: uuid.UUID,
    subject: str = Depends(current_subject),
    session: AsyncSession = Depends(get_session),
):
    """One run, its settings, and every step — the whole chart in one payload.

    One request rather than one per step: a run is a handful of steps and the
    chart needs all of them at once, so paging here would only add a loading
    state to a page that has nothing to page through.
    """
    run = await _load_visible_run(session, run_id, subject)
    splits, sources, steps_done = await _counts(session, [run_id])
    step_summaries = await _step_summaries(session, run_id)

    # The same question in both splits: allowed, warned about, never silent.
    overlap = (
        await session.scalars(
            select(OptimizationItem.item_key)
            .where(OptimizationItem.run_id == run_id)
            .group_by(OptimizationItem.item_key)
            .having(func.count(func.distinct(OptimizationItem.split)) > 1)
        )
    ).all()

    base = _run_out(
        run, splits.get(run_id, {}), sources.get(run_id, []), steps_done.get(run_id, 0)
    )
    return OptimizationRunDetail(
        **base.model_dump(),
        config=run.config or {},
        detector=run.detector or {},
        workspace_version=run.workspace_version,
        overlap_item_keys=sorted(overlap),
        steps=step_summaries,
    )


async def _step_summaries(
    session: AsyncSession, run_id: uuid.UUID
) -> list[OptimizationStepSummary]:
    """Every step of one run, with its two rollouts attached — the chart's data.

    Two queries, not one per step: the detail page and the progress snapshot
    both read this, and the snapshot is built while a stream is being set up.
    """
    step_rows = (
        await session.scalars(
            select(OptimizationStep)
            .where(OptimizationStep.run_id == run_id)
            .order_by(OptimizationStep.step_no)
        )
    ).all()
    rollouts = (
        await session.scalars(
            select(OptimizationRollout).where(
                OptimizationRollout.step_id.in_([s.id for s in step_rows] or [None])
            )
        )
    ).all()
    by_step: dict[uuid.UUID, dict[str, OptimizationRollout]] = {}
    for rollout in rollouts:
        by_step.setdefault(rollout.step_id, {})[rollout.split] = rollout
    return [_step_summary(s, by_step.get(s.id, {})) for s in step_rows]


async def _one_run_out(session: AsyncSession, run: OptimizationRun) -> OptimizationRunOut:
    """The list-shaped view of a single run, for the endpoints that mutate one."""
    splits, sources, steps_done = await _counts(session, [run.id])
    return _run_out(
        run, splits.get(run.id, {}), sources.get(run.id, []), steps_done.get(run.id, 0)
    )


def _num(value):
    return float(value) if value is not None else None


def _step_summary(step: OptimizationStep, rollouts: dict) -> OptimizationStepSummary:
    train = rollouts.get("train")
    val = rollouts.get("val")
    return OptimizationStepSummary(
        step_no=step.step_no,
        epoch_no=step.epoch_no,
        step_in_epoch=step.step_in_epoch,
        parent_step_no=step.parent_step_no,
        status=step.status,
        gate_action=step.gate_action,
        gate_reject_reason=step.gate_reject_reason,
        retried=step.retried,
        abort_reason=step.abort_reason,
        train_hard=_num(train.hard) if train else None,
        train_soft=_num(train.soft) if train else None,
        train_activation_rate=_num(train.activation_rate) if train else None,
        train_n_scored=train.n_scored if train else None,
        train_n_items=train.n_items if train else None,
        train_n_agent_error=train.n_agent_error if train else None,
        train_n_judge_error=train.n_judge_error if train else None,
        train_latency_min_ms=train.latency_min_ms if train else None,
        train_latency_p50_ms=train.latency_p50_ms if train else None,
        train_latency_max_ms=train.latency_max_ms if train else None,
        val_hard=_num(val.hard) if val else None,
        val_soft=_num(val.soft) if val else None,
        val_activation_rate=_num(val.activation_rate) if val else None,
        val_n_scored=val.n_scored if val else None,
        val_n_items=val.n_items if val else None,
        val_n_agent_error=val.n_agent_error if val else None,
        val_n_judge_error=val.n_judge_error if val else None,
        val_latency_min_ms=val.latency_min_ms if val else None,
        val_latency_p50_ms=val.latency_p50_ms if val else None,
        val_latency_max_ms=val.latency_max_ms if val else None,
        lines_added=step.lines_added,
        lines_removed=step.lines_removed,
        files_touched=step.files_touched,
        n_edits_applied=step.n_edits_applied,
        n_edits_skipped=step.n_edits_skipped,
        edit_summary=step.edit_summary,
        skill_len=step.skill_len,
        candidate_from_cache=step.candidate_from_cache,
        current_score=_num(step.current_score),
        best_score=_num(step.best_score),
        started_at=step.started_at,
        completed_at=step.completed_at,
    )


@router.post("/runs/{run_id}/cancel", response_model=OptimizationRunOut)
async def cancel_optimization_run(
    run_id: uuid.UUID,
    subject: str = Depends(current_subject),
    session: AsyncSession = Depends(get_session),
):
    """Stop a running optimization. Creator only.

    Narrower than an eval run's cancel, which any reader may call: an eval run
    belongs to an eval set several people share, while an optimization run is
    one developer's experiment against their own agent endpoint. Its steps are
    kept — the loop finishes nothing further, but every completed step and its
    best skill remain downloadable.
    """
    run = await _load_visible_run(session, run_id, subject)
    if run.created_by != subject:
        raise HTTPException(
            status_code=403, detail="only the developer who started this run can cancel it"
        )
    if run.status not in ("pending", "running"):
        raise HTTPException(
            status_code=409, detail=f"this run is already {run.status}; nothing to cancel"
        )
    run.cancel_requested = True
    await session.commit()
    # The flag is the durable record and survives a restart; the event is what
    # interrupts the agent call already in flight (app/cancellation.py).
    cancellation.signal(run_id)
    return await _one_run_out(session, run)


@router.post("/runs/{run_id}/resume", response_model=OptimizationRunOut)
async def resume_optimization_run(
    run_id: uuid.UUID,
    subject: str = Depends(current_subject),
    session: AsyncSession = Depends(get_session),
):
    """Continue a run the backend restart left `interrupted`. Creator only.

    Only `interrupted` — not `cancelled` and not `failed`. A cancelled run was
    stopped deliberately and restarting it under the same id would erase that
    decision from the record; a failed one stopped because continuing would
    produce a misleading result, which resuming does not fix. Both remain
    available as the starting point for a new run.

    The engine re-derives everything it needs from the completed steps, so there
    is nothing to pass here: it picks up at the step after the last one that
    finished.
    """
    run = await _load_visible_run(session, run_id, subject)
    if run.created_by != subject:
        raise HTTPException(
            status_code=403, detail="only the developer who started this run can resume it"
        )
    if run.status != "interrupted":
        raise HTTPException(
            status_code=409,
            detail=f"only an interrupted run can be resumed; this one is {run.status}",
        )
    run.status = "running"
    run.error_message = None
    run.cancel_requested = False
    await session.commit()
    cancellation.clear(run_id)
    runner.start(run_id)
    return await _one_run_out(session, run)


@router.get("/runs/{run_id}/progress")
async def optimization_progress(
    run_id: uuid.UUID,
    request: Request,
    # `current_subject` rather than a session-consuming dependency, for the
    # reason spelled out in `routers/runs.py:run_progress`: a stream that lasts
    # as long as the run would hold a pooled connection for that whole time.
    subject: str = Depends(current_subject),
):
    """SSE stream of one run's progress: a snapshot, then live step events.

    Same shape and same ordering rules as the eval run stream — authorize,
    subscribe, then read — because the hazards are identical and one of them is
    specific to how long these run: subscribing after the read would let a
    `run_completed` published in between fall on the floor, leaving the page
    pinging every fifteen seconds against a run that finished an hour ago.

    The steps come along in the snapshot rather than being fetched separately.
    A late subscriber — someone opening the page mid-run, or after a laptop woke
    up — needs the whole chart, and an SSE stream that only carries the future
    would leave them with a blank one until the next step landed. That can be
    several minutes.
    """
    async with SessionLocal() as session:
        queue = hub.subscribe(run_id)
        try:
            run = await _load_visible_run(session, run_id, subject)
            status = run.status
            steps = await _step_summaries(session, run_id)
            snapshot = {
                "status": status,
                "step_no": max((s.step_no for s in steps), default=0),
                "total_steps": run.total_steps,
                "best_step": run.best_step,
                "best_score": _num(run.best_score),
                "server_time": datetime.now(timezone.utc).isoformat(),
                "steps": [s.model_dump(mode="json") for s in steps],
            }
            # `commit`, not `rollback`: rollback expires every loaded object and
            # the values read above would go stale before the generator starts.
            await session.commit()
        except BaseException:
            hub.unsubscribe(run_id, queue)
            raise

    async def event_gen():
        try:
            yield {"event": "snapshot", "data": json.dumps(snapshot)}
            if status not in ("running", "pending"):
                yield {"event": "run_completed", "data": json.dumps({"status": status})}
                return

            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield resync_or_ping(queue)
                    continue
                dropped = resync_if_dropped(queue)
                if dropped:
                    yield dropped
                yield {"event": event.get("type", "message"), "data": json.dumps(event)}
                if event.get("type") == "run_completed":
                    break
        finally:
            hub.unsubscribe(run_id, queue)

    return EventSourceResponse(event_gen())


@router.delete("/runs/{run_id}", status_code=204)
async def delete_optimization_run(
    run_id: uuid.UUID,
    subject: str = Depends(current_subject),
    session: AsyncSession = Depends(get_session),
):
    """Delete a run and everything under it. Creator only."""
    run = await _load_visible_run(session, run_id, subject)
    if run.created_by != subject:
        raise HTTPException(
            status_code=403, detail="only the developer who started this run can delete it"
        )
    if run.status == "running":
        raise HTTPException(
            status_code=409, detail="cancel this run before deleting it"
        )
    await session.delete(run)
    await session.commit()
