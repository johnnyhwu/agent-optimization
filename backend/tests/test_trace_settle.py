"""Settling a trace that has started arriving (§6.12a).

The bug this covers: `wait_for_trace` used to return the first read that came
back with any observation at all. Langfuse ingestion is incremental, so that read
can land mid-flush — and the span that loses the race is systematically the last
one, the agent's final answer generation, because it ends immediately before the
HTTP response that sends this platform looking for the trace. The developer saw a
trace that stopped at the second-to-last tool call, and the diagnosis compared a
truncated trace against the expected process.

Two invariants run through everything below:

  * settling may only ever **add** to the trace already in hand. A shorter read,
    a NotReady, or an error during settling is discarded, never adopted.
  * a read that adds nothing ends the wait. Growth is the only reason to keep
    reading, so the steady-state cost is one extra request.
"""
from __future__ import annotations

import asyncio

import pytest

from app.integrations.base import NOT_READY, Span, Trace, TraceFetchError
from app.pipeline import settle_trace, wait_for_trace


def trace_of(n: int, tag: str = "") -> Trace:
    """A trace with `n` spans; `tag` marks *which read* produced it."""
    return Trace(
        correlation_id="corr",
        spans=[
            Span(
                index=i, tool_name=f"step{i}", status="success",
                input="in", output=f"out{i}{tag}", token_usage={},
            )
            for i in range(n)
        ],
    )


class ScriptedTraceClient:
    """Returns a scripted sequence of results, one per `fetch_trace` call.

    A `TraceFetchError` in the script is raised rather than returned. The last
    entry repeats, so a test only lists the reads it cares about.
    """

    def __init__(self, *results):
        self.results = list(results)
        self.calls = 0

    async def fetch_trace(self, correlation_id: str):
        self.calls += 1
        result = self.results[min(self.calls - 1, len(self.results) - 1)]
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def never_cancelled() -> asyncio.Event:
    return asyncio.Event()


@pytest.fixture(autouse=True)
def settle_enabled(configure):
    """Three settle reads, no sleeping — the defaults minus the wall clock."""
    with configure(trace_settle_max_reads=3, trace_settle_delay_s=0.0):
        yield


# --- settle_trace ------------------------------------------------------------

async def test_the_late_final_span_is_picked_up(never_cancelled):
    """The bug, reduced: the last span arrives after the first read."""
    client = ScriptedTraceClient(trace_of(4))
    settled = await settle_trace("corr", client, trace_of(3), never_cancelled)
    assert len(settled.spans) == 4
    assert settled.spans[-1].tool_name == "step3"


async def test_a_read_that_adds_nothing_ends_the_wait(never_cancelled):
    """Steady state costs one extra request, not `trace_settle_max_reads` of them."""
    client = ScriptedTraceClient(trace_of(3))
    settled = await settle_trace("corr", client, trace_of(3), never_cancelled)
    assert len(settled.spans) == 3
    assert client.calls == 1


async def test_growth_keeps_it_reading_up_to_the_cap(never_cancelled):
    """A trace that grows on every read is bounded, not followed forever."""
    client = ScriptedTraceClient(trace_of(2), trace_of(3), trace_of(4), trace_of(5))
    settled = await settle_trace("corr", client, trace_of(1), never_cancelled)
    assert client.calls == 3  # trace_settle_max_reads
    assert len(settled.spans) == 4  # whatever the last allowed read saw


async def test_an_equal_read_is_still_adopted(never_cancelled):
    """Same span count, later read: the bodies are more complete, so take it.

    Langfuse fills an observation's output in after creating it, so a span can be
    present but empty on the read that first saw it.
    """
    client = ScriptedTraceClient(trace_of(3, tag="-late"))
    settled = await settle_trace("corr", client, trace_of(3, tag="-early"), never_cancelled)
    assert [s.output for s in settled.spans] == ["out0-late", "out1-late", "out2-late"]


async def test_a_shorter_read_is_never_adopted(never_cancelled):
    """Settling adds; it must not be able to take spans away."""
    client = ScriptedTraceClient(trace_of(2))
    settled = await settle_trace("corr", client, trace_of(4), never_cancelled)
    assert len(settled.spans) == 4


async def test_not_ready_during_settling_keeps_what_we_have(never_cancelled):
    client = ScriptedTraceClient(NOT_READY)
    settled = await settle_trace("corr", client, trace_of(3), never_cancelled)
    assert len(settled.spans) == 3


async def test_an_error_during_settling_keeps_what_we_have(never_cancelled):
    """A trace store that breaks on the confirmation read must not cost us the
    trace we already read successfully."""
    client = ScriptedTraceClient(TraceFetchError("langfuse fell over"))
    settled = await settle_trace("corr", client, trace_of(3), never_cancelled)
    assert len(settled.spans) == 3


async def test_cancellation_stops_settling_immediately(never_cancelled):
    never_cancelled.set()
    client = ScriptedTraceClient(trace_of(9))
    settled = await settle_trace("corr", client, trace_of(3), never_cancelled)
    assert client.calls == 0
    assert len(settled.spans) == 3


async def test_settling_can_be_turned_off(configure, never_cancelled):
    """`TRACE_SETTLE_MAX_READS=0` restores take-the-first-read exactly."""
    with configure(trace_settle_max_reads=0):
        client = ScriptedTraceClient(trace_of(4))
        settled = await settle_trace("corr", client, trace_of(3), never_cancelled)
    assert client.calls == 0
    assert len(settled.spans) == 3


async def test_the_delay_is_actually_waited(configure, never_cancelled):
    """The knob is a wall-clock gap, not a no-op — the whole point is to give
    ingestion time to land."""
    with configure(trace_settle_max_reads=1, trace_settle_delay_s=0.05):
        client = ScriptedTraceClient(trace_of(3))
        loop = asyncio.get_running_loop()
        started = loop.time()
        await settle_trace("corr", client, trace_of(3), never_cancelled)
        assert loop.time() - started >= 0.05


# --- wait_for_trace: settling is wired into the wait -------------------------

async def test_wait_for_trace_settles_what_it_polled_for(configure, never_cancelled):
    """Polling and settling compose: NotReady until the trace appears, then the
    confirmation reads."""
    with configure(trace_poll_backoff_s=[0.0], trace_poll_max_attempts=5):
        client = ScriptedTraceClient(NOT_READY, NOT_READY, trace_of(3), trace_of(4), trace_of(4))
        trace, error = await wait_for_trace("corr", client, never_cancelled)
    assert error is None
    assert len(trace.spans) == 4


async def test_wait_for_trace_reports_no_error_when_only_settling_failed(
    configure, never_cancelled
):
    """A failure on a confirmation read is not a trace error: the question has a
    trace, and `trace_error` is what the UI turns into a red banner (§9.5)."""
    with configure(trace_poll_backoff_s=[0.0], trace_poll_max_attempts=5):
        client = ScriptedTraceClient(trace_of(3), TraceFetchError("boom"))
        trace, error = await wait_for_trace("corr", client, never_cancelled)
    assert error is None
    assert len(trace.spans) == 3


async def test_wait_for_trace_still_gives_up_and_reports_why(configure, never_cancelled):
    """Unchanged behaviour: nothing ever arrives, the last error is returned."""
    with configure(trace_poll_backoff_s=[0.0], trace_poll_max_attempts=3):
        client = ScriptedTraceClient(TraceFetchError("langfuse is down"))
        trace, error = await wait_for_trace("corr", client, never_cancelled)
    assert trace is None
    assert "langfuse is down" in error


async def test_wait_for_trace_returns_not_ready_as_no_error(configure, never_cancelled):
    """Ingestion that simply never lands is not an error to display."""
    with configure(trace_poll_backoff_s=[0.0], trace_poll_max_attempts=3):
        client = ScriptedTraceClient(NOT_READY)
        trace, error = await wait_for_trace("corr", client, never_cancelled)
    assert trace is None and error is None
