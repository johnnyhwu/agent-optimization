"""Async SQLAlchemy engine + session factory.

**The pool is the backend's real concurrency limit.** SQLAlchemy's defaults are
`pool_size=5, max_overflow=10`, and the deployed form runs a single uvicorn
worker on purpose (see docker-compose.prod.yml), so those fifteen connections
are the whole application's budget. That was invisible while the only data came
from `seed.py` and one developer was clicking around; with a room full of users
it is the first thing to run out, and it runs out as
`QueuePool limit of size 5 overflow 10 reached, connection timed out` — an error
that names the pool rather than whatever was holding it.

Two rules keep that budget honest, and both matter more than the numbers below:

1. **Nothing may hold a session across a slow external call.** A request that
   waits on Langfuse or an LLM while its session is open occupies a connection
   for the length of that call, and `Depends(get_session)` keeps the session
   alive until the *response* ends — which for an SSE stream is the length of a
   whole run. `routers/runs.py:run_progress` and the trace/diagnosis paths open
   short-lived sessions and commit before the outbound call for exactly this
   reason.
2. **End the transaction with `commit()`, never `rollback()`.** Both return the
   connection to the pool, but `rollback()` also expires every ORM object in the
   session, so the next attribute read becomes a lazy load and raises
   `MissingGreenlet` in async context. `expire_on_commit=False` below is what
   makes the `commit()` form safe.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

# `pool_size + max_overflow` connections, per worker. Postgres' own
# `max_connections` (100 by default, and the compose file does not override it)
# is the ceiling this has to stay under — so raising the pool and adding workers
# are the same decision, taken together, not two independent knobs.
engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout_s,
    pool_recycle=settings.db_pool_recycle_s,
    pool_pre_ping=settings.db_pool_pre_ping,
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: one session per request.

    Note "per request", not "per handler": FastAPI tears this down after the
    response is finished, so a streaming response holds its connection for as
    long as it streams. Endpoints that stream, or that wait on an external
    service, should open their own `SessionLocal()` block instead — see the
    module docstring.
    """
    async with SessionLocal() as session:
        yield session
