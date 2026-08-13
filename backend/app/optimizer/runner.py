"""Background entry point for an optimization run, and its own reaper.

The counterpart of `orchestrator.run_eval`: a run is an `asyncio.create_task` in
this process, with its own session, and this is where it starts and where it is
cleaned up after.

The reaper is separate from `main.reap_interrupted_runs` and behaves differently
on purpose. An eval run that was executing when the backend went down is written
off as `failed`, because there is nothing to resume — its questions were answered
in parallel and half of them are simply missing. An optimization run is
checkpointed per step, so the same event leaves something a developer can pick
back up: it becomes `interrupted`, which is a status an eval run has no use for,
and `POST /resume` continues from the last completed step. Writing those off as
failed would throw away an hour of paid-for rollouts every deploy.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import uuid

from sqlalchemy import update

from app import cancellation
from app.db import SessionLocal
from app.integrations import build_seams
from app.models import OptimizationRun
from app.optimizer.engine import run_optimization
from app.optimizer.store import DbOptimizationStore

log = logging.getLogger(__name__)


async def run_optimization_task(run_id: uuid.UUID) -> None:
    """Execute one optimization run to completion, whatever happens.

    Every failure path inside the engine already finalises the run and publishes
    a terminal event; this catches the ones that cannot — a session that will
    not open, a seam that raises while being built — so that a run can never be
    left `running` by an exception thrown before the loop began.
    """
    try:
        async with SessionLocal() as session:
            store = DbOptimizationStore(session)
            spec = await store.load_run(run_id)
            if spec is None:
                return
            # `include_optimizer=True` is what distinguishes this from an eval
            # run's seams: the model that writes skill edits is built only for
            # the one path that uses it, so a misconfigured optimizer endpoint
            # cannot break evaluation.
            # `include_workspace` for the version probe only: a step records the
            # agent config it ran against, so a deploy midway through a run is
            # visible rather than merely moving the accuracy.
            seams = build_seams(
                spec.config, spec.secrets, include_optimizer=True, include_workspace=True,
            )
            await run_optimization(
                run_id, store=store, seams=seams,
                cancel_event=cancellation.event_for(run_id),
            )
    except Exception:  # noqa: BLE001 - last line of defence
        log.exception("optimization run %s could not be started", run_id)
        await _finalize_unstarted(run_id)
    finally:
        cancellation.clear(run_id)


def start(run_id: uuid.UUID) -> asyncio.Task:
    """Spawn the run as a background task, as `trigger_run` does for an eval."""
    return asyncio.create_task(run_optimization_task(run_id))


async def _finalize_unstarted(run_id: uuid.UUID) -> None:
    async with SessionLocal() as session:
        run = await session.get(OptimizationRun, run_id)
        if run is None or run.status not in ("pending", "running"):
            return
        run.status = "failed"
        run.error_message = "this run could not be started; see the server log"
        run.completed_at = dt.datetime.now(dt.timezone.utc)
        await session.commit()


async def reap_interrupted_optimization_runs(session_factory=None) -> int:
    """Mark runs this process can no longer be executing as `interrupted`.

    Deliberately *not* `failed`. The distinction is the whole reason the status
    exists: an interrupted run has every completed step on disk, a downloadable
    best skill, and a resume button. Calling it failed would present an hour of
    finished work as a dead end, and the developer's only recourse would be to
    pay for it again.

    Safe at startup for the same reason the eval reaper is (single worker,
    `docs/spec.md` §5.3): no other process could legitimately be running these.
    """
    async with (session_factory or SessionLocal)() as session:
        result = await session.execute(
            update(OptimizationRun)
            .where(OptimizationRun.status == "running")
            .values(
                status="interrupted",
                error_message="the backend restarted; this run can be resumed",
            )
        )
        await session.commit()
        return result.rowcount or 0
