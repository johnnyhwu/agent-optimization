"""In-memory cancellation signals for running evals.

`runs.cancel_requested` in the database is the durable truth — it is what the UI
reads and what survives a restart. This registry is the *fast* half: an
`asyncio.Event` the orchestrator can race against an in-flight agent call, so a
cancel takes effect immediately instead of after the current question finishes.
A real agent question can take tens of seconds, which is exactly the case the
stop button exists for.

Single-process POC, same shape as `app/sse.py`: one backend process owns the
background tasks, so an in-process registry is enough.
"""
from __future__ import annotations

import asyncio
import uuid

_events: dict[uuid.UUID, asyncio.Event] = {}


def event_for(run_id: uuid.UUID) -> asyncio.Event:
    """The cancel event for a run, created on first use."""
    event = _events.get(run_id)
    if event is None:
        event = asyncio.Event()
        _events[run_id] = event
    return event


def signal(run_id: uuid.UUID) -> None:
    """Request cancellation. Safe to call for a run that never started."""
    event_for(run_id).set()


def is_cancelled(run_id: uuid.UUID) -> bool:
    event = _events.get(run_id)
    return event is not None and event.is_set()


def clear(run_id: uuid.UUID) -> None:
    """Drop the event once the run is finalized, so the map can't grow forever."""
    _events.pop(run_id, None)
