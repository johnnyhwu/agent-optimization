"""The run-progress SSE stream must not hold a pooled database connection.

`Depends(get_session)` is torn down when the *response* ends. For every other
endpoint that is the same thing as "when the handler returns"; for this one the
response is a stream that stays open until the run finishes, so an injected
session sat idle-in-transaction on one of the pool's connections for the entire
run. The deployed form runs a single uvicorn worker, so that pool is the whole
backend's budget — a couple of dozen people watching their runs was enough to
exhaust it and make every unrelated endpoint fail with
"QueuePool limit of size 5 overflow 10 reached, connection timed out", which is
what the users saw as the page freezing.

These need a real database, because the property under test is what the
connection pool is doing, which a stub session cannot answer. They **skip**
unless `TEST_DATABASE_URL` is set, matching test_pagination.py:

    createdb agenteval_test
    TEST_DATABASE_URL='postgresql+asyncpg://localhost/agenteval_test' \
        pytest tests/test_progress_stream.py
"""
from __future__ import annotations

import os
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models import EvalSet, EvalSetRole, Question, QuestionResult, Run
from app.routers import runs as runs_router
from app.sse import hub

TEST_DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="set TEST_DATABASE_URL to run the progress-stream tests"
)


class FakeRequest:
    """Stands in for the Request the handler polls for client disconnects.

    `disconnected=True` makes the live-run loop exit on its first pass, which is
    what keeps a test of an in-progress run from blocking on the queue forever.
    """

    def __init__(self, disconnected: bool = True):
        self._disconnected = disconnected

    async def is_disconnected(self) -> bool:
        return self._disconnected


@pytest.fixture
async def engine():
    # Deliberately tiny: with pool_size=1 and no overflow, a single leaked
    # connection is the difference between the second checkout succeeding and
    # hanging. That makes the leak assertable rather than merely visible.
    eng = create_async_engine(TEST_DB, pool_size=1, max_overflow=0, pool_timeout=2)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session_factory(engine, monkeypatch):
    """Point the router's SessionLocal at the test engine.

    Same injection point test_orchestrator.py uses for the orchestrator's own
    session factory.
    """
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        await s.execute(
            text(
                "TRUNCATE span_analyses, question_results, question_skills,"
                " questions, runs, eval_set_roles, eval_sets CASCADE"
            )
        )
        await s.commit()
    monkeypatch.setattr(runs_router, "SessionLocal", maker)
    return maker


async def make_run(session_factory, *, status="running", subject="alice", role="owner",
                   questions=3, done=1, correct=1):
    async with session_factory() as s:
        es = EvalSet(name="set", source_format="jsonl", meta={})
        s.add(es)
        await s.flush()
        if role is not None:
            s.add(EvalSetRole(eval_set_id=es.id, user_subject=subject, role=role))
        run = Run(eval_set_id=es.id, triggered_by=subject, status=status,
                  config={}, secrets={})
        s.add(run)
        await s.flush()
        for i in range(questions):
            q = Question(eval_set_id=es.id, question_id=f"q_{i}", question="q",
                         ground_truth_response="gt", ground_truth_reasoning="r")
            s.add(q)
            await s.flush()
            s.add(QuestionResult(
                run_id=run.id, question_pk=q.id, correlation_id=uuid.uuid4().hex,
                status="done" if i < done else "pending",
                verdict="correct" if i < correct else None,
                trace_ready=False,
            ))
        await s.commit()
        return es.id, run.id


async def drain(response, limit=10):
    events = []
    async for chunk in response.body_iterator:
        events.append(chunk)
        if len(events) >= limit:
            break
    return events


# --- The property this file exists for ---------------------------------------


async def test_no_connection_is_held_while_the_stream_is_open(engine, session_factory):
    """The regression test for the pool exhaustion.

    Checked *before* the stream is consumed, which is where the old code had
    already taken its connection and would keep it for the length of the run.
    """
    eval_set_id, run_id = await make_run(session_factory)

    response = await runs_router.run_progress(
        eval_set_id=eval_set_id, run_id=run_id,
        request=FakeRequest(), subject="alice",
    )

    assert engine.pool.checkedout() == 0, (
        "the progress stream is holding a pooled connection before it has even "
        "started streaming; it must not depend on get_session"
    )

    # And the pool is genuinely usable, not merely reporting zero: with
    # pool_size=1/max_overflow=0 this checkout is exactly the one that used to
    # time out behind a stream.
    async with session_factory() as s:
        assert (await s.scalar(text("select 1"))) == 1
        await s.commit()

    await drain(response)
    assert engine.pool.checkedout() == 0


async def test_connection_stays_free_across_many_concurrent_streams(engine, session_factory):
    """Fifteen simultaneous viewers used to be the whole pool. Now they cost none."""
    eval_set_id, run_id = await make_run(session_factory)

    responses = [
        await runs_router.run_progress(
            eval_set_id=eval_set_id, run_id=run_id,
            request=FakeRequest(), subject="alice",
        )
        for _ in range(15)
    ]

    assert engine.pool.checkedout() == 0
    async with session_factory() as s:
        assert (await s.scalar(text("select 1"))) == 1
        await s.commit()

    for response in responses:
        await drain(response)


# --- Everything the rewrite had to preserve ----------------------------------


async def test_snapshot_reports_the_runs_current_counts(session_factory):
    eval_set_id, run_id = await make_run(
        session_factory, questions=3, done=2, correct=1
    )
    response = await runs_router.run_progress(
        eval_set_id=eval_set_id, run_id=run_id,
        request=FakeRequest(), subject="alice",
    )
    events = await drain(response)

    body = "".join(str(e) for e in events)
    assert "snapshot" in body
    assert '"total": 3' in body
    assert '"done": 2' in body
    assert '"correct": 1' in body
    assert '"status": "running"' in body


async def test_finished_run_gets_a_terminal_event_and_closes(session_factory):
    """The frontend subscribes unconditionally; a historical run must end at once
    rather than leaving the client waiting on a stream that will never speak."""
    eval_set_id, run_id = await make_run(session_factory, status="completed")
    response = await runs_router.run_progress(
        eval_set_id=eval_set_id, run_id=run_id,
        request=FakeRequest(disconnected=False), subject="alice",
    )
    events = await drain(response)

    body = "".join(str(e) for e in events)
    assert "snapshot" in body
    assert "run_completed" in body


async def test_viewer_may_watch(session_factory):
    eval_set_id, run_id = await make_run(session_factory, role="viewer")
    response = await runs_router.run_progress(
        eval_set_id=eval_set_id, run_id=run_id,
        request=FakeRequest(), subject="alice",
    )
    await drain(response)


async def test_a_stranger_is_refused_before_the_response_starts(session_factory):
    """403 has to be raised, not yielded.

    The frontend treats 401/403/404 as permanent and anything else as worth
    retrying with backoff (src/api.js). An authorization failure that leaked into
    the generator would reach the browser as a 200 followed by a dead stream, and
    the client would reconnect forever.
    """
    eval_set_id, run_id = await make_run(session_factory, subject="alice")

    with pytest.raises(HTTPException) as exc:
        await runs_router.run_progress(
            eval_set_id=eval_set_id, run_id=run_id,
            request=FakeRequest(), subject="mallory",
        )
    assert exc.value.status_code == 403


async def test_unknown_run_is_a_404(session_factory):
    eval_set_id, _run_id = await make_run(session_factory)

    with pytest.raises(HTTPException) as exc:
        await runs_router.run_progress(
            eval_set_id=eval_set_id, run_id=uuid.uuid4(),
            request=FakeRequest(), subject="alice",
        )
    assert exc.value.status_code == 404


async def test_a_refused_caller_leaves_no_subscription_behind(session_factory):
    """Subscribing before the checks would leak a queue per rejected request, and
    the orchestrator would then publish into it forever."""
    eval_set_id, run_id = await make_run(session_factory, subject="alice")
    before = len(hub._subscribers.get(run_id, []))

    with pytest.raises(HTTPException):
        await runs_router.run_progress(
            eval_set_id=eval_set_id, run_id=run_id,
            request=FakeRequest(), subject="mallory",
        )

    assert len(hub._subscribers.get(run_id, [])) == before


async def test_events_published_before_the_stream_is_read_are_not_lost(session_factory):
    """Subscription happens in the handler, not on first iteration.

    The orchestrator publishes as it goes, and the gap between "handler returned"
    and "client starts reading" is real. Anything published in it has to be
    waiting in the queue, which is why `hub.subscribe` is called eagerly — and
    why it is called before the snapshot counts are read.
    """
    eval_set_id, run_id = await make_run(session_factory)

    response = await runs_router.run_progress(
        eval_set_id=eval_set_id, run_id=run_id,
        request=FakeRequest(disconnected=False), subject="alice",
    )
    await hub.publish(run_id, {"type": "question_done", "question_pk": "abc"})
    await hub.publish(run_id, {"type": "run_completed", "status": "completed"})

    body = "".join(str(e) for e in await drain(response))
    assert "question_done" in body
    assert "run_completed" in body


async def test_a_backed_up_run_stream_says_resync_rather_than_losing_the_end(
    session_factory, configure
):
    """Mailboxes are bounded, so a stalled subscriber loses its oldest events
    (app/sse.py). `run_completed` is among the events that can be lost, and a
    client waiting on a terminal event that was already discarded waits for the
    rest of the page's life — the progress bar stuck at "running" forever.

    So a drop is always reported. The client refetches the run and finds out how
    it ended, which is the only recovery available once the event is gone.
    """
    eval_set_id, run_id = await make_run(session_factory)

    with configure(sse_queue_max_events=2):
        response = await runs_router.run_progress(
            eval_set_id=eval_set_id, run_id=run_id,
            request=FakeRequest(disconnected=False), subject="alice",
        )
        for i in range(5):
            await hub.publish(run_id, {"type": "question_done", "n": i})

        events = await drain(response, limit=4)

    names = [e.get("event") for e in events]
    assert names == ["snapshot", "resync", "question_done", "question_done"]
    # Drop-oldest, so it is the newest that survive.
    assert '"n": 3' in str(events[2]) and '"n": 4' in str(events[3])


async def test_the_stream_unsubscribes_when_it_ends(session_factory):
    eval_set_id, run_id = await make_run(session_factory, status="completed")
    response = await runs_router.run_progress(
        eval_set_id=eval_set_id, run_id=run_id,
        request=FakeRequest(disconnected=False), subject="alice",
    )
    await drain(response)

    assert hub._subscribers.get(run_id) in (None, [])


async def test_a_failed_snapshot_does_not_leak_the_subscription(session_factory,
                                                                monkeypatch):
    """Subscription happens before the snapshot is read, so the window between
    them has to be covered. The queue is unbounded and the orchestrator keeps
    publishing into it for the rest of the run."""
    eval_set_id, run_id = await make_run(session_factory)
    before = len(hub._subscribers.get(run_id, []))

    class SnapshotFails:
        """A session whose *snapshot* queries raise, leaving everything else real.

        The first `scalar` is the role lookup in `role_for`, and it has to
        succeed — otherwise the request dies before subscribing and the test
        passes without ever exercising the window it exists to cover.
        """

        def __init__(self, inner):
            self._inner = inner

        async def __aenter__(self):
            session = await self._inner.__aenter__()
            real_scalar = session.scalar
            calls = {"n": 0}

            async def scalar(*args, **kwargs):
                calls["n"] += 1
                if calls["n"] == 1:  # role_for
                    return await real_scalar(*args, **kwargs)
                raise RuntimeError("database went away mid-snapshot")

            session.scalar = scalar
            return session

        async def __aexit__(self, *exc):
            return await self._inner.__aexit__(*exc)

    maker = runs_router.SessionLocal
    monkeypatch.setattr(runs_router, "SessionLocal", lambda: SnapshotFails(maker()))

    with pytest.raises(RuntimeError):
        await runs_router.run_progress(
            eval_set_id=eval_set_id, run_id=run_id,
            request=FakeRequest(), subject="alice",
        )

    assert len(hub._subscribers.get(run_id, [])) == before


async def test_unknown_run_leaves_no_subscription_behind(session_factory):
    """The 404 now happens after `hub.subscribe`, so it has to clean up.

    Subscribing first is what closes the run-completes-mid-handler race; this is
    the bill for it, and an unbounded queue nobody ever drains is exactly the
    kind of leak that only shows up as memory growth days later.
    """
    eval_set_id, _run_id = await make_run(session_factory)
    missing = uuid.uuid4()

    with pytest.raises(HTTPException) as exc:
        await runs_router.run_progress(
            eval_set_id=eval_set_id, run_id=missing,
            request=FakeRequest(), subject="alice",
        )

    assert exc.value.status_code == 404
    assert hub._subscribers.get(missing) in (None, [])


async def test_a_run_finishing_during_the_handler_still_terminates_the_stream(
    session_factory, monkeypatch
):
    """The race the subscribe-first ordering exists to close.

    The orchestrator publishes `run_completed` exactly once. The dangerous
    window is between the handler reading `runs.status` (still 'running', so the
    stream will wait for a terminal event) and the stream subscribing — an event
    landing there is published to nobody, and the client waits forever on a
    progress bar stuck at "running".

    Publishing after the handler returns would prove nothing: by then every
    ordering has subscribed. So the publish is injected *into* the run read
    itself, which is the last moment that still precedes a subscribe placed
    after it.
    """
    eval_set_id, run_id = await make_run(session_factory, status="running")

    maker = runs_router.SessionLocal

    class PublishesDuringTheRunRead:
        def __init__(self, inner):
            self._inner = inner

        async def __aenter__(self):
            session = await self._inner.__aenter__()
            real_get = session.get

            async def get(model, pk, *args, **kwargs):
                obj = await real_get(model, pk, *args, **kwargs)
                if model is Run:
                    # The orchestrator finishes the run right here.
                    await hub.publish(
                        run_id, {"type": "run_completed", "status": "completed"}
                    )
                return obj

            session.get = get
            return session

        async def __aexit__(self, *exc):
            return await self._inner.__aexit__(*exc)

    monkeypatch.setattr(
        runs_router, "SessionLocal", lambda: PublishesDuringTheRunRead(maker())
    )

    response = await runs_router.run_progress(
        eval_set_id=eval_set_id, run_id=run_id,
        request=FakeRequest(disconnected=False), subject="alice",
    )

    body = "".join(str(e) for e in await drain(response, limit=4))
    assert "run_completed" in body, (
        "the stream never terminated: the terminal event was published while "
        "nobody was subscribed"
    )
