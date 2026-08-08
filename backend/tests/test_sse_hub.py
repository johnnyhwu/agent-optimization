"""The progress hub itself: topics, fan-out, and what happens when a client stalls.

Both properties here are load-bearing for the live views, and neither is visible
from a router test:

  * **A topic is any hashable value.** Runs publish to a run id, the playground
    publishes to both an attempt id and the owning subject. The second is what
    lets one stream follow everything a person is running, instead of one stream
    per attempt — which is the bug the per-user stream was built to fix.
  * **A mailbox is bounded, and overflow is reported rather than absorbed.** A
    subscriber that stops reading must not be able to grow a queue for the life
    of its stream, and the playground's stream lives as long as the tab does.
    Dropping is only safe because it is announced: the client refetches.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from app.sse import ProgressHub, Subscription, resync_if_dropped, resync_or_ping


@pytest.fixture
def hub():
    return ProgressHub()


# --- Topics -----------------------------------------------------------------

async def test_a_subject_string_is_as_good_a_topic_as_a_run_id(hub):
    """Widening the key from UUID to anything hashable is what made the per-user
    playground stream possible at all."""
    by_id, by_subject = hub.subscribe(uuid.uuid4()), hub.subscribe("alice")

    await hub.publish("alice", {"type": "attempt_started"})

    assert by_subject.get_nowait()["type"] == "attempt_started"
    assert by_id.empty()


async def test_every_subscriber_on_a_topic_gets_every_event(hub):
    """Two tabs watching the same run are two subscribers, not a race for one."""
    run_id = uuid.uuid4()
    first, second = hub.subscribe(run_id), hub.subscribe(run_id)

    await hub.publish(run_id, {"type": "question_done"})

    assert not first.empty() and not second.empty()


async def test_publishing_to_a_topic_nobody_watches_is_a_no_op(hub):
    """The old shape of the bug: an attempt published into a topic with no
    subscriber, and the event was simply gone. Harmless in itself — what made it
    a bug was that the *only* subscription was to the open attempt."""
    await hub.publish(uuid.uuid4(), {"type": "attempt_completed"})  # must not raise


async def test_unsubscribing_stops_delivery_and_forgets_the_topic(hub):
    topic = uuid.uuid4()
    sub = hub.subscribe(topic)
    hub.unsubscribe(topic, sub)

    await hub.publish(topic, {"type": "question_done"})

    assert sub.empty()
    # Not merely emptied: the topic itself is dropped, or a long-lived process
    # accumulates an entry per run it has ever served.
    assert topic not in hub._subscribers


async def test_unsubscribing_twice_is_harmless(hub):
    """Every stream unsubscribes in a `finally`, and some paths can reach it
    after an earlier cleanup."""
    topic = uuid.uuid4()
    sub = hub.subscribe(topic)
    hub.unsubscribe(topic, sub)
    hub.unsubscribe(topic, sub)  # must not raise


async def test_one_subscriber_leaving_does_not_disturb_the_other(hub):
    topic = uuid.uuid4()
    staying, leaving = hub.subscribe(topic), hub.subscribe(topic)
    hub.unsubscribe(topic, leaving)

    await hub.publish(topic, {"type": "question_done"})

    assert not staying.empty()
    assert leaving.empty()


# --- Bounded mailboxes ------------------------------------------------------

async def test_a_full_mailbox_keeps_the_newest_and_counts_what_it_dropped():
    sub = Subscription(maxsize=3)
    for i in range(10):
        sub.offer({"n": i})

    assert [sub.get_nowait()["n"] for _ in range(3)] == [7, 8, 9]
    assert sub.take_dropped() == 7


async def test_the_drop_count_resets_once_reported():
    """The stream emits one `resync` per gap, not one per dropped event."""
    sub = Subscription(maxsize=1)
    sub.offer({"n": 0})
    sub.offer({"n": 1})

    assert sub.take_dropped() == 1
    assert sub.take_dropped() == 0


async def test_publishing_never_blocks_on_a_stalled_subscriber(hub):
    """`publish` is awaited inside the orchestrator's per-question loop, so a
    blocking put would let one stuck browser stall a run for everybody. The
    mailbox drops instead — and the run keeps going."""
    topic = uuid.uuid4()
    hub.subscribe(topic)  # never read from

    for i in range(2000):
        await asyncio.wait_for(hub.publish(topic, {"n": i}), timeout=1.0)


async def test_a_slow_subscriber_cannot_grow_without_limit(hub):
    topic = uuid.uuid4()
    sub = hub.subscribe(topic)

    for i in range(5000):
        await hub.publish(topic, {"n": i})

    drained = 0
    while not sub.empty():
        sub.get_nowait()
        drained += 1
    assert drained <= 512, f"mailbox grew to {drained}"


async def test_one_slow_subscriber_does_not_cost_a_healthy_one_anything(hub, configure):
    """Drops are per subscriber. A stalled tab must not punch holes in the
    stream of someone whose connection is fine."""
    topic = uuid.uuid4()
    with configure(sse_queue_max_events=2):
        stalled, healthy = hub.subscribe(topic), hub.subscribe(topic)

        received = []
        for i in range(20):
            await hub.publish(topic, {"n": i})
            while not healthy.empty():
                received.append(healthy.get_nowait()["n"])  # keeps up

    assert received == list(range(20)), "a healthy subscriber missed an event"
    assert healthy.take_dropped() == 0
    assert stalled.take_dropped() == 18


# --- What the streams do about it -------------------------------------------

def test_a_gap_is_reported_as_resync_and_only_once():
    sub = Subscription(maxsize=1)
    sub.offer({"n": 0})
    sub.offer({"n": 1})

    assert resync_if_dropped(sub) == {"event": "resync", "data": "{}"}
    assert resync_if_dropped(sub) is None


def test_the_keepalive_carries_the_gap_when_there_is_one():
    """An idle stream still has to speak every 15s or a proxy closes it. When the
    idleness follows an overflow, that beat is the earliest chance to say so."""
    sub = Subscription(maxsize=1)
    assert resync_or_ping(sub) == {"event": "ping", "data": "{}"}

    sub.offer({"n": 0})
    sub.offer({"n": 1})
    assert resync_or_ping(sub) == {"event": "resync", "data": "{}"}
    assert resync_or_ping(sub) == {"event": "ping", "data": "{}"}


async def test_dropping_the_oldest_is_what_protects_a_terminal_event(hub, configure):
    """Which end gets dropped decides whether a stream can lose its own ending.

    On a run's topic the producer is one orchestrator and `run_completed` is the
    last thing it ever publishes, so dropping the *oldest* means the terminal
    event cannot be evicted — there is nothing published after it to push it out.
    Dropping the newest instead would make "the run finished but the page still
    says running" a routine outcome under load.

    This is the guarantee, pinned down so a future change to the eviction end has
    to argue with a test rather than slip through.
    """
    topic = uuid.uuid4()
    with configure(sse_queue_max_events=2):
        sub = hub.subscribe(topic)
        for i in range(50):
            await hub.publish(topic, {"type": "question_done", "n": i})
        await hub.publish(topic, {"type": "run_completed", "status": "completed"})

    delivered = []
    while not sub.empty():
        delivered.append(sub.get_nowait())
    assert delivered[-1]["type"] == "run_completed"
    assert sub.take_dropped() == 49


async def test_a_terminal_event_CAN_be_lost_when_the_topic_has_other_producers(
    hub, configure
):
    """The playground's per-user topic is the case that is genuinely at risk.

    Every attempt this subject runs publishes to it, so one attempt's
    `attempt_completed` is not the newest event on the topic for long — a second
    attempt still running pushes it out. That is the original bug's shape
    exactly: a row that never settles. It cannot be prevented, only reported, so
    the client is told to resync and refetches.
    """
    with configure(sse_queue_max_events=2):
        sub = hub.subscribe("alice")
        await hub.publish("alice", {"type": "attempt_completed", "attempt_id": "A"})
        for i in range(5):
            await hub.publish("alice", {"type": "attempt_answered", "attempt_id": "B"})

    delivered = [sub.get_nowait() for _ in range(2)]
    assert all(e["attempt_id"] == "B" for e in delivered), "A's ending was evicted"
    assert resync_if_dropped(sub) == {"event": "resync", "data": "{}"}


async def test_a_mailbox_of_one_still_delivers(hub):
    """The degenerate bound. `maxsize` is clamped to at least 1, so a
    misconfigured 0 cannot turn every stream into silence."""
    sub = Subscription(maxsize=0)
    sub.offer({"n": 1})
    assert sub.get_nowait() == {"n": 1}
