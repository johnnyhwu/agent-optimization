"""Run endpoints: trigger, list, live SSE progress, cancel, delete
(§6.13 / §6.15 / §9.14)."""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app import cancellation
from app.auth import current_subject, require_owner, require_reader, role_for
from app.db import SessionLocal, get_session
from app.models import EvalSet, QuestionResult, Run
from app.orchestrator import agent_version, run_eval
from app.schemas import RunConfig, RunCreate, RunOut, RunPage, RunRename
from app import settings_catalog
from app.services import judge_prompt, run_config, user_secrets, user_settings
from app.services.deletion import delete_run as delete_run_rows
from app.sse import hub, resync_if_dropped, resync_or_ping

# A credential and the endpoint it authenticates against, for the reuse rule
# below: {secret key in runs.secrets: endpoint key in runs.config}.
#
# Read from the catalogue rather than written out again. The same pairing now
# governs a *saved* credential (services/user_secrets.py), and two copies of
# "which URL does this key authenticate against" is one copy too many — the one
# that fell behind would be the one deciding where a credential gets sent.
_SECRET_ENDPOINTS = dict(settings_catalog.SECRET_ENDPOINTS)

# The same credentials as UI-facing slot names. Only these names are ever sent
# outward — the values behind them stay in the database.
_SECRET_SLOTS = {"llm_api_key": "llm", "langfuse_secret_key": "langfuse"}

router = APIRouter(prefix="/eval-sets/{eval_set_id}/runs", tags=["runs"])


def _now_iso() -> str:
    """This server's clock, for a client rendering durations against it."""
    return datetime.now(timezone.utc).isoformat()

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


async def _judge_invalid_count(session: AsyncSession, run_id: uuid.UUID) -> int:
    return (
        await session.scalar(
            select(func.count())
            .select_from(QuestionResult)
            .where(
                QuestionResult.run_id == run_id,
                QuestionResult.failure_kind == "judge_invalid",
            )
        )
    ) or 0


async def _judge_invalid_counts(
    session: AsyncSession, run_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """How many questions this run could not judge, per run.

    A second grouped query rather than a join onto the incorrect counts: the two
    are counting different things (a verdict vs. the absence of one) and keeping
    them apart means neither can accidentally start filtering the other.
    """
    if not run_ids:
        return {}
    rows = (
        await session.execute(
            select(QuestionResult.run_id, func.count())
            .where(
                QuestionResult.run_id.in_(run_ids),
                QuestionResult.failure_kind == "judge_invalid",
            )
            .group_by(QuestionResult.run_id)
        )
    ).all()
    return {run_id: n for run_id, n in rows}


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
    subject: str | None = None,
) -> dict:
    """The credentials this run executes with, highest precedence first: what was
    typed into this request, then anything borrowed from an earlier run, then the
    caller's own saved default.

    A credential from either of the last two is only carried over when the
    endpoint it authenticates against is unchanged. Without that rule a user
    could reuse a stored key while pointing the base URL at a server they
    control, and the backend would happily send someone else's credential there.
    The saved default obeys the same rule for the same reason, enforced in
    `services/user_secrets.inject`.
    """
    secrets = {k: v for k, v in body.secrets.model_dump().items() if v}
    if body.reuse_secrets_from_run_id is None:
        return await _with_saved_defaults(session, subject, config, secrets)

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
    return await _with_saved_defaults(session, subject, config, secrets)


async def _with_saved_defaults(
    session: AsyncSession, subject: str | None, config: dict, secrets: dict
) -> dict:
    """Fill any credential still missing from the caller's saved defaults.

    Last in the order deliberately: something typed into this request, or
    borrowed from the run being copied, is a more specific statement of intent
    than a preference set weeks ago.
    """
    if not subject or not user_secrets.available():
        return secrets
    stored = await user_settings.stored_secrets(session, subject)
    return user_secrets.inject(stored, config, secrets)


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
    # The grading criteria come from the eval set, never from the request body —
    # anyone may start a run (§6.16), but only an owner decides what counts as
    # correct. `resolve` discards whatever was posted for these three fields.
    system, user = judge_prompt.effective(es.judge_system_prompt, es.judge_user_prompt)
    config = run_config.resolve(
        body.config,
        judge_prompt=(system, user, judge_prompt.fingerprint(system, user)),
    )
    secrets = await _resolve_secrets(session, eval_set_id, body, config, subject)

    run = Run(
        eval_set_id=eval_set_id, triggered_by=subject, status="running",
        name=(body.name or "").strip() or None, config=config, secrets=secrets,
        # What the run is about to measure against. Best-effort by design: an
        # agent that will not answer this costs the run its drift check, not its
        # start — the Run eval dialog's own pre-flight already refused to enable
        # Start against an agent that is not there.
        workspace_version=await agent_version(config, secrets),
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)

    task = asyncio.create_task(run_eval(run.id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return _run_out(run, incorrect_count=0, judge_invalid_count=0)


def _run_out(run: Run, incorrect_count: int, judge_invalid_count: int = 0) -> RunOut:
    return RunOut(
        id=run.id, eval_set_id=run.eval_set_id, triggered_by=run.triggered_by,
        name=run.name, config=RunConfig(**(run.config or {})),
        credentials_set=_credentials_set(run),
        workspace_version=run.workspace_version,
        workspace_version_end=run.workspace_version_end,
        status=run.status, cancel_requested=bool(run.cancel_requested),
        started_at=run.started_at, completed_at=run.completed_at,
        pass_rate=float(run.pass_rate) if run.pass_rate is not None else None,
        total_count=run.total_count, correct_count=run.correct_count,
        incorrect_count=incorrect_count,
        judge_invalid_count=judge_invalid_count,
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

    run_ids = [r.id for r in runs]
    counts = await _incorrect_counts(session, run_ids)
    unjudged = await _judge_invalid_counts(session, run_ids)
    items = [_run_out(r, counts.get(r.id, 0), unjudged.get(r.id, 0)) for r in runs]
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
    return _run_out(
        run,
        await _incorrect_count(session, run_id),
        await _judge_invalid_count(session, run_id),
    )


@router.patch("/{run_id}", response_model=RunOut)
async def rename_run(
    eval_set_id: uuid.UUID,
    run_id: uuid.UUID,
    body: RunRename,
    subject: str = Depends(require_reader),
    session: AsyncSession = Depends(get_session),
):
    """Rename a run.

    A name could only be set when the run was triggered, which is the one moment
    a developer does not yet know what the run will turn out to be about — so
    almost every run carried a timestamp forever, and a list of timestamps is not
    a list anyone can read.

    Permission mirrors cancel rather than delete: a viewer may trigger a run
    (§6.16), and a name is the label on their own work. Owners can rename
    anyone's; a viewer can rename the runs they started. Nothing about the run's
    results changes, which is why this is not owner-only.
    """
    run = await session.get(Run, run_id)
    if run is None or run.eval_set_id != eval_set_id:
        raise HTTPException(status_code=404, detail="run not found")
    if run.triggered_by != subject:
        role = await role_for(session, eval_set_id, subject)
        if role != "owner":
            raise HTTPException(
                status_code=403,
                detail="only an owner or the person who started this run can rename it",
            )

    run.name = body.name
    await session.commit()
    return _run_out(
        run,
        await _incorrect_count(session, run_id),
        await _judge_invalid_count(session, run_id),
    )


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
    return _run_out(
        run,
        await _incorrect_count(session, run_id),
        await _judge_invalid_count(session, run_id),
    )


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
    # `current_subject` rather than `require_reader`: the latter is itself a
    # `Depends(get_session)` consumer, and FastAPI caches sub-dependencies, so
    # asking for it would keep a session alive for the whole stream no matter
    # what this signature says. The role check it performs is done by hand
    # below, against the same table with the same rule.
    subject: str = Depends(current_subject),
):
    """SSE stream of live run progress (§6.15). Emits an initial snapshot, then
    live per-question events until the run completes.

    **This endpoint deliberately does not take `Depends(get_session)`.** That
    dependency is torn down when the *response* ends, and this response ends
    when the run does — so a session injected here is held, idle in transaction,
    for minutes at a time. The pool is 20 + 10 per worker and there is one
    worker (see app/db.py), so a few dozen people watching their runs was enough
    to exhaust it and take down every other endpoint with
    `QueuePool limit ... connection timed out`. Everything that needs the
    database is done in the short block below, and the generator never touches
    it. `routers/playground.py:attempt_progress` — the same stream for a
    playground attempt — has always been written this way.

    Ordering inside that block is load-bearing: authorize, subscribe, then read
    the run and its counts. Subscribing before those reads is what stops an
    event published in between — including the run's own `run_completed` — from
    being dropped; authorizing before subscribing is what stops a rejected
    caller from leaking a subscription; and the `try` around the reads covers
    the 404, which now happens after the subscription exists.
    """
    async with SessionLocal() as session:
        role = await role_for(session, eval_set_id, subject)
        if role not in ("owner", "viewer"):
            raise HTTPException(status_code=403, detail="no access to this eval set")

        # Subscribe before the run is read, not after. The status this handler
        # reads is the one the stream reports, and it decides whether to wait for
        # a terminal event at all — so a run that finishes between the read and
        # the subscription would leave the stream waiting for a `run_completed`
        # that was published while nobody was listening, pinging every 15s
        # forever. Subscribing first makes the two orders equivalent: either the
        # read already sees the finished run and the stream ends immediately, or
        # the terminal event is sitting in this queue.
        queue = hub.subscribe(run_id)

        # Once subscribed, every exit from here has to unsubscribe: the queue is
        # unbounded and the orchestrator publishes into it for the rest of the
        # run, so a 404 or a failed snapshot query would otherwise leave a queue
        # growing behind a request that never streamed. The generator's own
        # `finally` only covers the path where the generator actually starts.
        try:
            run = await session.get(Run, run_id)
            if run is None or run.eval_set_id != eval_set_id:
                raise HTTPException(status_code=404, detail="run not found")
            # Read off the ORM object now: the generator runs after this block
            # has exited, and passing plain values keeps it incapable of
            # triggering a lazy load (which in async context surfaces as
            # MissingGreenlet).
            run_status = run.status

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
            # Ends the read transaction and hands the connection back before the
            # stream starts. `commit`, not `rollback`: rollback expires every
            # loaded object, and `run_status` above would be the last safe read.
            await session.commit()
        except BaseException:
            hub.unsubscribe(run_id, queue)
            raise

    async def event_gen():
        try:
            yield {"event": "snapshot",
                   "data": json.dumps({"status": run_status, "done": done,
                                       "total": total, "correct": correct,
                                       # The question list renders elapsed times
                                       # as `now - started_at`, and the browser's
                                       # clock is not this one. One timestamp per
                                       # connection is all the client needs to
                                       # correct for the difference.
                                       "server_time": _now_iso()})}
            if run_status != "running":
                yield {"event": "run_completed",
                       "data": json.dumps({"status": run_status})}
                return

            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield resync_or_ping(queue)
                    continue
                # Mailboxes are bounded (app/sse.py), so a subscriber that
                # stopped reading loses its oldest events rather than growing
                # without limit. `run_completed` is among the events that can be
                # lost, and a client waiting for one that has already been
                # discarded waits forever — so a drop is always reported.
                dropped = resync_if_dropped(queue)
                if dropped:
                    yield dropped
                yield {"event": event.get("type", "message"), "data": json.dumps(event)}
                if event.get("type") == "run_completed":
                    break
        finally:
            hub.unsubscribe(run_id, queue)

    return EventSourceResponse(event_gen())
