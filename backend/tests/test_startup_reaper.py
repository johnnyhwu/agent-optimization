"""Runs left mid-flight by a backend restart (deployment form).

A run is an `asyncio.create_task` background task in the backend process (§6.1).
When that process goes away — a deploy, a crash, an OOM kill — `runs.status` is
still 'running' and nothing will ever change it again: the UI waits forever on a
run that cannot finish, and `POST /cancel` rejects it as already terminal. There
is no path out of that state without this.

Needs a real database, like the paging tests, and skips without
`TEST_DATABASE_URL`:

    TEST_DATABASE_URL='postgresql+asyncpg://localhost/agenteval_test' \\
        pytest tests/test_startup_reaper.py
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.main import reap_interrupted_runs
from app.models import EvalSet, Run

TEST_DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="set TEST_DATABASE_URL to run the startup-reaper tests"
)


@pytest.fixture
async def factory():
    engine = create_async_engine(TEST_DB)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def seed_runs(factory, statuses: list[str]) -> list[uuid.UUID]:
    async with factory() as session:
        es = EvalSet(name="set", source_format="jsonl", meta={}, version=1)
        session.add(es)
        await session.flush()
        ids = []
        for status in statuses:
            run = Run(eval_set_id=es.id, triggered_by="alice", status=status,
                      config={}, secrets={})
            session.add(run)
            await session.flush()
            ids.append(run.id)
        await session.commit()
        return ids


async def statuses_of(factory, ids):
    async with factory() as session:
        return [(await session.get(Run, i)).status for i in ids]


async def test_a_running_run_is_closed_out_with_a_reason(factory):
    [run_id] = await seed_runs(factory, ["running"])

    assert await reap_interrupted_runs(factory) == 1

    async with factory() as session:
        run = await session.get(Run, run_id)
    assert run.status == "failed"
    # A bare 'failed' after a restart is indistinguishable from an agent that
    # broke, which sends someone looking in the wrong place (§4.11).
    assert "restarted" in run.error_message
    # Without this the run has no end time and reads as still in flight in any
    # view that sorts or filters on it.
    assert run.completed_at is not None


async def test_finished_runs_are_left_alone(factory):
    """Rewriting a completed run would falsify the history the whole cross-run
    comparison rests on (§4.6)."""
    ids = await seed_runs(factory, ["completed", "failed", "cancelled"])

    assert await reap_interrupted_runs(factory) == 0
    assert await statuses_of(factory, ids) == ["completed", "failed", "cancelled"]


async def test_only_the_running_ones_are_touched(factory):
    ids = await seed_runs(factory, ["completed", "running", "cancelled", "running"])

    assert await reap_interrupted_runs(factory) == 2
    assert await statuses_of(factory, ids) == ["completed", "failed", "cancelled", "failed"]


async def test_it_is_safe_to_run_twice(factory):
    """Startup is not once-per-database; a container that restarts twice in a row
    must not keep rewriting rows it already closed."""
    await seed_runs(factory, ["running"])

    assert await reap_interrupted_runs(factory) == 1
    assert await reap_interrupted_runs(factory) == 0


async def test_an_empty_database_is_not_an_error(factory):
    """The very first boot, before anyone has triggered anything."""
    assert await reap_interrupted_runs(factory) == 0
