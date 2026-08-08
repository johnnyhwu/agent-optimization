"""In-memory progress hub for SSE (§6.15).

Single-process POC: producers publish progress events; an SSE endpoint subscribes
to a topic. This is the one-way, short-lived run-progress channel — NOT the
(out-of-scope) edit-sync channel of §6.16.

**A topic is any hashable value, not just a run id.** Runs publish to their
`run_id`; the playground publishes to *both* an attempt's id and the owning
subject, so one stream can follow every attempt a person has running rather than
only the one they happen to have open. Widening the key is what makes the second
of those possible — see `routers/playground.py:playground_progress`.

**Queues are bounded, and overflow is reported rather than absorbed.** A
subscriber that stops reading — a stalled TCP connection, a laptop that slept —
must not be able to grow a queue for as long as its stream is open, and a
per-user playground stream stays open far longer than any run does. So a full
queue drops its *oldest* event and counts the drop; the stream then emits a
single `resync` event and the client refetches authoritative state.

That works because nothing downstream reconstructs state by replaying events: an
event is a "this changed, here is its current state" ping, and a refetch answers
the same question completely. Blocking the publisher instead would be far worse
than dropping — `publish` is awaited from inside the orchestrator's per-question
loop, so one stuck browser would stall a run for everybody.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Hashable

from app.config import settings


class Subscription:
    """One subscriber's bounded mailbox on a topic.

    Deliberately exposes the same four methods the plain `asyncio.Queue` it
    replaced did (`get`, `get_nowait`, `empty`, and being passed back to
    `unsubscribe`), so every existing call site and test reads unchanged.
    """

    def __init__(self, maxsize: int) -> None:
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max(maxsize, 1))
        # Events discarded since the last time the stream asked. Not a bool: the
        # count is worth having in a log line when someone asks why a client kept
        # resyncing.
        self._dropped = 0

    def offer(self, event: dict) -> None:
        """Add an event, discarding the oldest if the mailbox is full.

        Never blocks and never raises, because the caller is a producer in the
        middle of a run.
        """
        while True:
            try:
                self._queue.put_nowait(event)
                return
            except asyncio.QueueFull:
                try:
                    self._queue.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover - maxsize >= 1
                    return
                self._dropped += 1

    def take_dropped(self) -> int:
        """How many events were dropped since this was last asked, and reset."""
        dropped, self._dropped = self._dropped, 0
        return dropped

    async def get(self) -> dict:
        return await self._queue.get()

    def get_nowait(self) -> dict:
        return self._queue.get_nowait()

    def empty(self) -> bool:
        return self._queue.empty()


class ProgressHub:
    def __init__(self) -> None:
        self._subscribers: dict[Hashable, list[Subscription]] = defaultdict(list)

    def subscribe(self, topic: Hashable) -> Subscription:
        sub = Subscription(settings.sse_queue_max_events)
        self._subscribers[topic].append(sub)
        return sub

    def unsubscribe(self, topic: Hashable, sub: Subscription) -> None:
        subs = self._subscribers.get(topic)
        if subs and sub in subs:
            subs.remove(sub)
        if subs is not None and not subs:
            self._subscribers.pop(topic, None)

    async def publish(self, topic: Hashable, event: dict) -> None:
        """Fan an event out to a topic's subscribers.

        Stays `async` for its callers' sake even though nothing here awaits: the
        delivery is non-blocking by design (see the module docstring), and making
        that a property of this function rather than of every call site is what
        keeps a slow reader from ever reaching a producer.
        """
        for sub in list(self._subscribers.get(topic, [])):
            sub.offer(event)


hub = ProgressHub()
