"""In-memory per-run progress hub for SSE (§6.15).

Single-process POC: the orchestrator publishes run-progress events; the SSE
endpoint subscribes per run_id. This is the one-way, short-lived run-progress
channel — NOT the (out-of-scope) edit-sync channel of §6.16.
"""
from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict


class ProgressHub:
    def __init__(self) -> None:
        self._subscribers: dict[uuid.UUID, list[asyncio.Queue]] = defaultdict(list)

    def subscribe(self, run_id: uuid.UUID) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers[run_id].append(q)
        return q

    def unsubscribe(self, run_id: uuid.UUID, q: asyncio.Queue) -> None:
        subs = self._subscribers.get(run_id)
        if subs and q in subs:
            subs.remove(q)
        if subs is not None and not subs:
            self._subscribers.pop(run_id, None)

    async def publish(self, run_id: uuid.UUID, event: dict) -> None:
        for q in list(self._subscribers.get(run_id, [])):
            await q.put(event)


hub = ProgressHub()
