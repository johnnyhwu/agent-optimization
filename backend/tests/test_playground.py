"""Playground: the ephemeral single-question path (§10).

Two rules carry most of these tests:

  * **Optional means never called.** With no expected answer the judge must not
    run; with no expected reasoning the diagnosis must not run. Asserting "no
    verdict appeared" would pass even if the LLM had been called and its answer
    thrown away — that is a bill, so the assertions count calls.
  * **An attempt is private scratch work.** Someone else's attempt is a 404, not
    a 403: whether one exists at a given id is not theirs to learn.

No database is involved, because the playground has no tables.
"""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from fastapi import HTTPException

from app import cancellation, fake_config as fc, playground
from app.integrations import Seams
from app.integrations.base import AgentResponse, Span, Trace, Verdict, Workspace
from app.integrations.real.prompts import build_diagnosis_messages
from app.playground import PlaygroundAttempt
from app.routers import playground as playground_router
from app.schemas import PlaygroundCreate, RunConfig
from app.sse import hub


# --- Stub seams -------------------------------------------------------------

class RecordingAgent:
    def __init__(self, response="the answer", failed=False, error=None, delay=0.0):
        self.calls: list[dict] = []
        self.response, self.failed, self.error, self.delay = response, failed, error, delay

    async def call(self, question, correlation_id, user_id, tags=None, workspace=None):
        self.calls.append({
            "question": question, "correlation_id": correlation_id,
            "user_id": user_id, "tags": tags, "workspace": workspace,
        })
        if self.delay:
            await asyncio.sleep(self.delay)
        return AgentResponse(
            response=self.response, correlation_id=correlation_id,
            failed=self.failed, error=self.error, latency_ms=12,
        )


class RecordingJudge:
    def __init__(self, verdict="incorrect", exc=None, delay=0.0):
        self.calls: list[tuple] = []
        self.verdict, self.exc, self.delay = verdict, exc, delay

    async def judge(self, question, response, ground_truth):
        self.calls.append((question, response, ground_truth))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc:
            raise self.exc
        return Verdict(verdict=self.verdict, score=0.3, comment="missing a figure")


class StubTrace:
    def __init__(self, trace=None, exc=None):
        self.calls = 0
        self._trace = trace if trace is not None else Trace(
            correlation_id="c",
            spans=[Span(index=0, tool_name="sql", status="success",
                        input="in", output="out", token_usage={})],
        )
        self.exc = exc

    async def fetch_trace(self, correlation_id):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self._trace


class RecordingDiagnosis:
    model_name = "stub-diagnosis"

    def __init__(self, exc=None):
        self.calls: list[tuple] = []
        self.exc = exc

    async def diagnose(self, trace, ground_truth_reasoning, judge_verdict):
        self.calls.append((trace, ground_truth_reasoning, judge_verdict))
        if self.exc:
            raise self.exc
        return {"overall_diagnosis": "span 0 looks thin",
                "suspects": [{"span_index": 0, "confidence": "medium",
                              "reason": "r", "evidence": "e"}],
                "caveat": None}


@pytest.fixture(autouse=True)
def clean_store():
    playground.clear()
    yield
    playground.clear()


@pytest.fixture
def seams():
    return Seams(agent=RecordingAgent(), judge=RecordingJudge(),
                 trace=StubTrace(), diagnosis=RecordingDiagnosis())


def make_attempt(subject="alice", **kwargs) -> PlaygroundAttempt:
    defaults = dict(
        id=uuid.uuid4(), subject=subject, question="how much did ACME owe?",
        ground_truth_response=None, ground_truth_reasoning=None,
        workspace=None, workspace_baseline=None, config={}, secrets={},
        correlation_id=uuid.uuid4().hex,
    )
    defaults.update(kwargs)
    return PlaygroundAttempt(**defaults)


async def execute(attempt, seams, monkeypatch):
    monkeypatch.setattr(playground, "build_seams", lambda *a, **k: seams)
    playground.add(attempt)
    await playground.execute(attempt.id)
    return attempt


class Collector:
    """Drains an attempt's SSE events, as tests/test_orchestrator.py does."""

    def __init__(self, attempt_id):
        self.attempt_id = attempt_id
        self.queue = hub.subscribe(attempt_id)

    def drain(self) -> list[dict]:
        events = []
        while not self.queue.empty():
            events.append(self.queue.get_nowait())
        return events

    def close(self):
        hub.unsubscribe(self.attempt_id, self.queue)


# --- The happy path and the two optional stages -----------------------------

async def test_full_attempt_walks_every_phase(seams, monkeypatch):
    attempt = make_attempt(
        ground_truth_response="ACME owed $42,180.",
        ground_truth_reasoning="Read the billing skill, then query invoices.",
    )
    await execute(attempt, seams, monkeypatch)

    assert attempt.status == "done"
    assert attempt.phase == "diagnosed"
    assert attempt.agent_response == "the answer"
    assert attempt.verdict == "incorrect"
    assert attempt.trace is not None
    assert attempt.analysis["overall_diagnosis"] == "span 0 looks thin"
    assert attempt.analysis_model == "stub-diagnosis"


async def test_no_expected_answer_means_the_judge_is_never_called(seams, monkeypatch):
    attempt = make_attempt(ground_truth_reasoning="Read the billing skill.")
    await execute(attempt, seams, monkeypatch)

    assert seams.judge.calls == []  # not "verdict is None" — the call is the cost
    assert attempt.verdict is None
    assert attempt.status == "done"
    # The trace and the diagnosis still happen: they are what the attempt was for.
    assert attempt.trace is not None
    assert attempt.analysis is not None


async def test_no_expected_reasoning_means_no_diagnosis_call(seams, monkeypatch):
    attempt = make_attempt(ground_truth_response="ACME owed $42,180.")
    await execute(attempt, seams, monkeypatch)

    assert seams.diagnosis.calls == []
    assert attempt.analysis is None
    assert attempt.verdict == "incorrect"
    assert attempt.phase == "traced"
    assert attempt.status == "done"


async def test_question_only_attempt_answers_and_traces(seams, monkeypatch):
    attempt = make_attempt()
    await execute(attempt, seams, monkeypatch)

    assert (seams.judge.calls, seams.diagnosis.calls) == ([], [])
    assert attempt.status == "done"
    assert attempt.phase == "traced"
    assert attempt.agent_response == "the answer"


async def test_diagnosis_without_a_verdict_is_told_nothing_was_graded(seams, monkeypatch):
    """Expected process, no expected answer: the diagnosis contract takes None.

    The prompt must say so rather than leaving it out, or the model infers the
    answer was wrong and hunts for a fault that may not exist (§10.4).
    """
    attempt = make_attempt(ground_truth_reasoning="Read the billing skill.")
    await execute(attempt, seams, monkeypatch)

    _trace, reasoning, verdict = seams.diagnosis.calls[0]
    assert verdict is None
    assert reasoning == "Read the billing skill."

    messages = build_diagnosis_messages(attempt.trace.spans, reasoning, None)
    assert "No judgement was made" in messages[1]["content"]
    # Still the §6.9 four-block order, with the judge block last.
    assert messages[1]["content"].index("# Expected reasoning process") < \
        messages[1]["content"].index("# Actual trace") < \
        messages[1]["content"].index("# Judge outcome")


async def test_verdict_is_passed_to_the_diagnosis_when_there_is_one(seams, monkeypatch):
    attempt = make_attempt(
        ground_truth_response="ACME owed $42,180.",
        ground_truth_reasoning="Read the billing skill.",
    )
    await execute(attempt, seams, monkeypatch)

    _trace, _reasoning, verdict = seams.diagnosis.calls[0]
    assert verdict.verdict == "incorrect"
    assert verdict.comment == "missing a figure"


# --- The trace is settled before the attempt keeps it (§6.12a) --------------
#
# An attempt keeps the trace it was handed and nothing re-reads it afterwards
# (§10), so a half-ingested read here is not a frame the developer sees once —
# it is the trace, for as long as the attempt exists. The span that loses that
# race is the last one: the agent's final answer generation, which ends
# immediately before the response that sends us looking for the trace.

class GrowingTrace:
    """A trace store that reveals one more span on each read, up to `final`."""

    def __init__(self, first: int, final: int):
        self.calls = 0
        self.first, self.final = first, final

    async def fetch_trace(self, correlation_id):
        self.calls += 1
        count = min(self.first + self.calls - 1, self.final)
        return Trace(
            correlation_id=correlation_id,
            spans=[
                Span(index=i, tool_name=f"step{i}", status="success",
                     input="in", output="out", token_usage={})
                for i in range(count)
            ],
        )


async def test_the_final_span_still_landing_is_waited_for(monkeypatch, configure):
    seams = Seams(agent=RecordingAgent(), judge=RecordingJudge(),
                  trace=GrowingTrace(first=3, final=4), diagnosis=RecordingDiagnosis())
    attempt = make_attempt()
    with configure(trace_settle_max_reads=3, trace_settle_delay_s=0.0):
        await execute(attempt, seams, monkeypatch)

    assert [s.tool_name for s in attempt.trace.spans] == [
        "step0", "step1", "step2", "step3",
    ]


async def test_the_diagnosis_sees_the_settled_trace(monkeypatch, configure):
    """The worse half of the bug: the diagnosis compares the trace against the
    expected process (§6.9), so a trace missing its last step invites a
    confident diagnosis of a failure that never happened."""
    seams = Seams(agent=RecordingAgent(), judge=RecordingJudge(),
                  trace=GrowingTrace(first=3, final=4), diagnosis=RecordingDiagnosis())
    attempt = make_attempt(ground_truth_reasoning="Read the skill, then answer.")
    with configure(trace_settle_max_reads=3, trace_settle_delay_s=0.0):
        await execute(attempt, seams, monkeypatch)

    diagnosed_trace = seams.diagnosis.calls[0][0]
    assert len(diagnosed_trace.spans) == 4


async def test_settling_does_not_stall_a_trace_that_is_already_whole(
    monkeypatch, configure
):
    """The cost when nothing is pending is one confirmation read, not the cap."""
    trace = GrowingTrace(first=2, final=2)
    seams = Seams(agent=RecordingAgent(), judge=RecordingJudge(),
                  trace=trace, diagnosis=RecordingDiagnosis())
    with configure(trace_settle_max_reads=3, trace_settle_delay_s=0.0):
        await execute(make_attempt(), seams, monkeypatch)

    assert trace.calls == 2  # the read that found it, plus one that confirmed it


# --- Workspace override -----------------------------------------------------

async def test_workspace_override_reaches_the_agent(seams, monkeypatch):
    from app.integrations.base import WorkspaceOverride

    override = WorkspaceOverride(
        config={"agents": {"defaults": {"model": "big"}}},
        skills={"billing/SKILL.md": "# Billing (edited)"},
    )
    attempt = make_attempt(workspace=override)
    await execute(attempt, seams, monkeypatch)

    assert seams.agent.calls[0]["workspace"] is override
    assert attempt.config_overrides == ["agents.defaults.model"]


async def test_edited_files_are_counted_against_the_snapshot_not_the_whole_set(
    seams, monkeypatch
):
    """`skills` is the complete file set, so only a baseline can say what changed.

    Without one the summary would report every file the agent has as edited,
    which is exactly the noise that makes a before/after comparison useless.
    """
    from app.integrations.base import WorkspaceOverride

    baseline = {
        "billing/SKILL.md": "# Billing",
        "billing/references/refunds.md": "# Refunds",
        "reporting/SKILL.md": "# Reporting",
    }
    attempt = make_attempt(
        workspace=WorkspaceOverride(skills={
            "billing/SKILL.md": "# Billing (edited)",     # changed
            "reporting/SKILL.md": "# Reporting",          # untouched
            "billing/references/new.md": "# New",         # added
            # billing/references/refunds.md is absent — deleted for this call
        }),
        workspace_baseline=baseline,
    )
    await execute(attempt, seams, monkeypatch)

    assert attempt.edited_skill_files == [
        "billing/SKILL.md",
        "billing/references/new.md",
        "billing/references/refunds.md",
    ]


async def test_no_override_reports_nothing_edited(seams, monkeypatch):
    attempt = make_attempt()
    await execute(attempt, seams, monkeypatch)

    assert seams.agent.calls[0]["workspace"] is None
    assert attempt.config_overrides == []
    assert attempt.edited_skill_files == []


async def test_the_override_is_visible_in_the_fake_trace(monkeypatch):
    """The one piece of evidence the platform can offer that an override landed.

    Nothing can verify that a real agent honoured an override — the proof is the
    text turning up in the trace's first system message, which the span view
    renders. The fake seam has to produce that same evidence, or a Docker-only
    demo would never show what "the override arrived" looks like.
    """
    from app.integrations.base import WorkspaceOverride
    from app.integrations.fake import (
        FakeAgentClient, FakeDiagnosisClient, FakeJudgeClient, FakeTraceClient,
    )

    monkeypatch.setattr(fc, "TRACE_NOT_READY_POLLS", 0)
    seams = Seams(
        agent=FakeAgentClient(), judge=FakeJudgeClient(),
        trace=FakeTraceClient(), diagnosis=FakeDiagnosisClient(),
    )
    attempt = make_attempt(
        workspace=WorkspaceOverride(
            config={"agents": {"defaults": {"model": "OVERRIDE-MODEL"}}},
            skills={"billing/SKILL.md": "# Billing OVERRIDE-MARKER-12345"},
        ),
    )
    await execute(attempt, seams, monkeypatch)

    system = attempt.trace.spans[0].input_json["messages"][0]["content"]
    assert "OVERRIDE-MARKER-12345" in system
    assert "agents.defaults.model='OVERRIDE-MODEL'" in system


async def test_a_plain_attempt_leaves_no_override_in_the_trace(monkeypatch):
    from app.integrations.fake import (
        FakeAgentClient, FakeDiagnosisClient, FakeJudgeClient, FakeTraceClient,
    )

    monkeypatch.setattr(fc, "TRACE_NOT_READY_POLLS", 0)
    seams = Seams(
        agent=FakeAgentClient(), judge=FakeJudgeClient(),
        trace=FakeTraceClient(), diagnosis=FakeDiagnosisClient(),
    )
    attempt = make_attempt()
    await execute(attempt, seams, monkeypatch)

    system = attempt.trace.spans[0].input_json["messages"][0]["content"]
    assert "overridden for this call" not in system


async def test_attempts_are_tagged_and_attributed_to_their_subject(seams, monkeypatch):
    attempt = make_attempt(subject="bob")
    await execute(attempt, seams, monkeypatch)

    call = seams.agent.calls[0]
    assert call["user_id"] == "bob"
    assert call["tags"] == ["playground"]
    assert call["correlation_id"] == attempt.correlation_id


# --- Failure policy ---------------------------------------------------------

async def test_agent_failure_records_the_reason(monkeypatch):
    seams = Seams(agent=RecordingAgent(response="", failed=True, error="agent said no"),
                  judge=RecordingJudge(), trace=StubTrace(),
                  diagnosis=RecordingDiagnosis())
    attempt = make_attempt(ground_truth_response="x")
    await execute(attempt, seams, monkeypatch)

    assert attempt.status == "failed"
    assert attempt.error_message == "agent said no"
    assert seams.judge.calls == []  # nothing to grade
    assert playground_router._trace_view(attempt).trace_state == "no_trace"


async def test_agent_exception_records_the_reason(monkeypatch):
    class Boom:
        async def call(self, *a, **k):
            raise RuntimeError("connection reset")

    seams = Seams(agent=Boom(), judge=RecordingJudge(), trace=StubTrace(),
                  diagnosis=RecordingDiagnosis())
    attempt = make_attempt()
    await execute(attempt, seams, monkeypatch)

    assert attempt.status == "failed"
    assert "connection reset" in attempt.error_message


async def test_judge_failure_keeps_the_answer_and_the_trace(monkeypatch):
    """A failed judge is not a failed attempt.

    Unlike a run — where an unjudged question would silently inflate a pass rate
    — nothing here aggregates. The answer and the trace are still the point, so
    the attempt finishes and the reason is on it.
    """
    seams = Seams(agent=RecordingAgent(), judge=RecordingJudge(exc=RuntimeError("judge 500")),
                  trace=StubTrace(), diagnosis=RecordingDiagnosis())
    attempt = make_attempt(ground_truth_response="x")
    await execute(attempt, seams, monkeypatch)

    assert attempt.status == "done"
    assert attempt.verdict is None
    assert "judge 500" in attempt.error_message
    assert attempt.trace is not None


async def test_diagnosis_failure_leaves_the_attempt_intact(monkeypatch):
    seams = Seams(agent=RecordingAgent(), judge=RecordingJudge(), trace=StubTrace(),
                  diagnosis=RecordingDiagnosis(exc=RuntimeError("model down")))
    attempt = make_attempt(ground_truth_response="x", ground_truth_reasoning="y")
    await execute(attempt, seams, monkeypatch)

    assert attempt.status == "done"
    assert attempt.verdict == "incorrect"
    assert attempt.analysis is None
    assert "model down" in attempt.diagnosis_error


async def test_trace_store_error_does_not_fail_the_attempt(monkeypatch, configure):
    seams = Seams(agent=RecordingAgent(), judge=RecordingJudge(),
                  trace=StubTrace(exc=RuntimeError("langfuse 401")),
                  diagnosis=RecordingDiagnosis())
    attempt = make_attempt()
    with configure(trace_poll_backoff_s=[0.0], trace_poll_max_attempts=1):
        await execute(attempt, seams, monkeypatch)

    assert attempt.status == "done"
    assert attempt.trace is None
    assert "langfuse 401" in attempt.trace_error
    assert playground_router._trace_view(attempt).trace_state == "error"


async def test_misconfigured_seams_are_reported_on_the_attempt(monkeypatch):
    """`build_seams` raises when a real seam has no endpoint. That is a sentence
    the developer needs to read, not a background task dying silently."""
    def boom(*a, **k):
        raise RuntimeError("JUDGE_IMPL=real but no judge model was given")

    monkeypatch.setattr(playground, "build_seams", boom)
    attempt = make_attempt()
    playground.add(attempt)
    await playground.execute(attempt.id)

    assert attempt.status == "failed"
    assert "no judge model" in attempt.error_message


# --- Cancellation -----------------------------------------------------------

async def test_cancel_abandons_the_in_flight_agent_call(monkeypatch):
    """Stop must not mean "stop in up to AGENT_TIMEOUT_S" — the timeout is
    exactly when the button gets pressed. The assertion is the wall clock: a
    30s agent call has to be abandoned, not awaited."""
    seams = Seams(agent=RecordingAgent(delay=30.0), judge=RecordingJudge(),
                  trace=StubTrace(), diagnosis=RecordingDiagnosis())
    attempt = make_attempt()
    monkeypatch.setattr(playground, "build_seams", lambda *a, **k: seams)
    playground.add(attempt)

    task = asyncio.create_task(playground.execute(attempt.id))
    await asyncio.sleep(0.05)
    cancellation.signal(attempt.id)
    await asyncio.wait_for(task, timeout=2.0)

    assert attempt.status == "cancelled"
    assert "Stopped" in attempt.error_message
    assert attempt.agent_response is None


async def test_cancel_while_judging_keeps_the_answer(monkeypatch):
    """The agent's answer has already been paid for; stopping must not discard it."""
    seams = Seams(agent=RecordingAgent(), judge=RecordingJudge(delay=30.0),
                  trace=StubTrace(), diagnosis=RecordingDiagnosis())
    attempt = make_attempt(ground_truth_response="x")
    monkeypatch.setattr(playground, "build_seams", lambda *a, **k: seams)
    playground.add(attempt)

    task = asyncio.create_task(playground.execute(attempt.id))
    while attempt.agent_response is None:
        await asyncio.sleep(0.01)
    cancellation.signal(attempt.id)
    await asyncio.wait_for(task, timeout=2.0)

    assert attempt.status == "cancelled"
    assert attempt.agent_response == "the answer"
    assert attempt.verdict is None


# --- Model calls per attempt ------------------------------------------------
#
# The evaluation page's left column carries this figure per question; the
# playground's left column is the same design and did not. Counted through the
# same `services.trace_view.count_llm_calls` the run path uses, so the two
# screens cannot come to different answers about what a model call is.

def _counted_trace(*usages) -> Trace:
    return Trace(
        correlation_id="c",
        spans=[
            Span(index=i, tool_name="step", status="success", input="i", output="o",
                 token_usage=usage)
            for i, usage in enumerate(usages)
        ],
    )


async def test_an_attempts_model_calls_are_counted_from_its_trace(seams, monkeypatch):
    # Two generations and a tool call between them: only the generations spent
    # tokens, and only they are what anyone means by "model calls".
    seams.trace = StubTrace(
        _counted_trace({"input": 90, "output": 40, "total": 130}, {},
                       {"input": 20, "output": 10, "total": 30})
    )
    attempt = make_attempt()
    await execute(attempt, seams, monkeypatch)

    assert playground.event_for(attempt, "attempt_completed")["llm_call_count"] == 2
    assert playground_router._out(attempt).llm_call_count == 2


async def test_an_attempt_with_no_trace_reports_no_count(seams, monkeypatch):
    """`null`, never `0`.

    "We never got the trace" and "the agent answered without asking a model
    anything" are different claims, and the second one is worth not making by
    accident — the same rule `count_llm_calls` already applies on the run path.
    """
    seams.trace = StubTrace(exc=RuntimeError("langfuse down"))
    attempt = make_attempt()
    await execute(attempt, seams, monkeypatch)

    assert attempt.trace is None
    assert playground.event_for(attempt, "attempt_completed")["llm_call_count"] is None
    assert playground_router._out(attempt).llm_call_count is None


# --- Progress events --------------------------------------------------------

async def test_events_cover_every_stage_and_carry_the_fingerprint(seams, monkeypatch):
    """The front end refetches on `phase|verdict|trace_ready|has_analysis`, so
    every event has to carry all four — the same contract as the run stream."""
    attempt = make_attempt(ground_truth_response="x", ground_truth_reasoning="y")
    collector = Collector(attempt.id)
    try:
        await execute(attempt, seams, monkeypatch)
        events = collector.drain()
    finally:
        collector.close()

    assert [e["type"] for e in events] == [
        "attempt_started", "attempt_answered", "attempt_judged",
        "attempt_traced", "attempt_completed",
    ]
    for event in events:
        assert {"phase", "verdict", "trace_ready", "has_analysis"} <= set(event)
        assert event["attempt_id"] == str(attempt.id)
    assert events[-1]["has_analysis"] is True
    assert events[-1]["status"] == "done"


async def test_no_judged_event_when_nothing_is_judged(seams, monkeypatch):
    attempt = make_attempt()
    collector = Collector(attempt.id)
    try:
        await execute(attempt, seams, monkeypatch)
        types = [e["type"] for e in collector.drain()]
    finally:
        collector.close()

    assert "attempt_judged" not in types
    assert types[-1] == "attempt_completed"


# --- The per-user stream ----------------------------------------------------
#
# The bug this stream exists to fix: the front end could only subscribe to one
# attempt at a time, so asking a second question closed the first one's stream
# and everything it published afterwards was dropped. The row stayed grey until
# it was clicked. These tests assert the property that makes that impossible —
# progress reaches the owner's stream whatever they happen to have open.

class FakeRequest:
    """Stands in for the Request the handler polls for client disconnects.

    `disconnected=True` makes the loop exit on its first pass, which is what
    keeps a test of this never-terminating stream from blocking forever.
    """

    def __init__(self, disconnected: bool = True):
        self._disconnected = disconnected

    async def is_disconnected(self) -> bool:
        return self._disconnected


async def drain(response, limit=10):
    """The stream's events as `(name, payload)` pairs.

    `body_iterator` yields the generator's own dicts (sse-starlette encodes them
    downstream), so the payload can be parsed rather than string-matched — which
    matters here because several of these tests are about a payload's *contents*,
    not merely about a word appearing somewhere in the frame.
    """
    events = []
    async for chunk in response.body_iterator:
        events.append((chunk.get("event"), json.loads(chunk.get("data") or "{}")))
        if len(events) >= limit:
            break
    return events


def names(events) -> list[str]:
    return [name for name, _ in events]


async def test_progress_for_an_unopened_attempt_still_reaches_its_owner(
    seams, monkeypatch
):
    """The regression test for the reported bug, stated directly.

    Nothing here subscribes to `attempt.id`. Before the per-subject topic existed
    that meant every event below went nowhere, which is exactly what happened to
    a second question asked while the first was still running.
    """
    attempt = make_attempt(subject="alice", ground_truth_response="x")
    response = await playground_router.playground_progress(
        request=FakeRequest(disconnected=False), subject="alice",
    )
    await execute(attempt, seams, monkeypatch)

    events = await drain(response, limit=6)
    assert names(events) == [
        "snapshot", "attempt_started", "attempt_answered", "attempt_judged",
        "attempt_traced", "attempt_completed",
    ]
    assert all(
        payload["attempt_id"] == str(attempt.id) for _name, payload in events[1:]
    )


async def test_the_snapshot_carries_every_attempt_and_the_server_clock():
    """What makes a reload recover attempts it never saw finish.

    A per-attempt stream could only ever snapshot the one attempt it was opened
    for, so a reload mid-flight left every other running attempt unaccounted for
    until it was clicked.
    """
    running, finished = make_attempt(), make_attempt()
    finished.status = "done"
    theirs = make_attempt(subject="bob")
    for attempt in (running, finished, theirs):
        playground.add(attempt)

    response = await playground_router.playground_progress(
        request=FakeRequest(), subject="alice",
    )
    (name, snapshot), = await drain(response, limit=1)

    assert name == "snapshot"
    ids = {a["attempt_id"] for a in snapshot["attempts"]}
    assert ids == {str(running.id), str(finished.id)}
    assert snapshot["server_time"]


async def test_another_subjects_attempts_never_reach_this_stream(seams, monkeypatch):
    """Attempts are private scratch work; the stream is no exception.

    Asserted against the topic rather than by reading alice's stream, because a
    stream with nothing to say is indistinguishable from a slow one until its
    15-second keepalive — and waiting that out in a unit test buys nothing the
    subscription itself cannot answer instantly.
    """
    theirs = make_attempt(subject="bob")
    alice = hub.subscribe("alice")
    bob = hub.subscribe("bob")
    try:
        await execute(theirs, seams, monkeypatch)
        assert alice.empty()
        assert not bob.empty()
    finally:
        hub.unsubscribe("alice", alice)
        hub.unsubscribe("bob", bob)


async def test_the_stream_unsubscribes_when_it_ends():
    response = await playground_router.playground_progress(
        request=FakeRequest(), subject="alice",
    )
    await drain(response)

    assert hub._subscribers.get("alice") in (None, [])


async def test_a_backed_up_subscriber_is_told_to_resync_rather_than_growing(configure):
    """The mailbox is bounded, so a stalled client cannot grow a queue for as
    long as its stream is open — and the playground's stream stays open for as
    long as the tab does.

    Dropping is safe only because it is *reported*: nothing reconstructs state
    from the event history, so `resync` plus a refetch recovers whatever the gap
    swallowed.
    """
    with configure(sse_queue_max_events=2):
        response = await playground_router.playground_progress(
            request=FakeRequest(disconnected=False), subject="alice",
        )
        for i in range(5):
            await hub.publish("alice", {"type": "attempt_answered", "n": i})

        events = await drain(response, limit=4)

    assert names(events) == ["snapshot", "resync", "attempt_answered", "attempt_answered"]
    # The two most recent survived; the three oldest were discarded.
    assert [payload["n"] for _name, payload in events[2:]] == [3, 4]


async def test_publishing_still_reaches_the_per_attempt_stream(seams, monkeypatch):
    """The per-attempt topic stays: `GET /attempts/{id}/progress` is still a
    valid single-attempt API, and dropping it would be a breaking change for
    anything driving one attempt at a time."""
    attempt = make_attempt()
    collector = Collector(attempt.id)
    try:
        await execute(attempt, seams, monkeypatch)
        types = [e["type"] for e in collector.drain()]
    finally:
        collector.close()

    assert types[0] == "attempt_started"
    assert types[-1] == "attempt_completed"


async def test_events_carry_the_timing_the_list_row_needs(seams, monkeypatch):
    """Carried on the event so a finished row needs no follow-up request.

    Once every attempt reports rather than only the open one, a refetch per
    completion would be one extra request per completion per person.
    """
    attempt = make_attempt()
    collector = Collector(attempt.id)
    try:
        await execute(attempt, seams, monkeypatch)
        events = collector.drain()
    finally:
        collector.close()

    assert events[0]["agent_started_at"] is not None
    assert events[-1]["agent_latency_ms"] == 12
    assert all(e["created_at"] for e in events)


async def test_the_agent_clock_starts_before_the_call_not_at_creation(
    seams, monkeypatch
):
    """`created_at` is when the request was accepted; the timer has to count from
    when the agent was actually asked. The workspace baseline lookup sits between
    the two, and charging it to the agent overstates every attempt that sent a
    skill override."""
    attempt = make_attempt()
    await execute(attempt, seams, monkeypatch)

    assert attempt.agent_started_at is not None
    assert attempt.agent_started_at >= attempt.created_at


# --- The store --------------------------------------------------------------

async def test_store_evicts_the_oldest_finished_attempt(configure):
    with configure(playground_max_attempts_per_user=2):
        kept = []
        for _ in range(3):
            attempt = make_attempt()
            attempt.status = "done"
            playground.add(attempt)
            kept.append(attempt)

    ids = [a.id for a in playground.list_for("alice")]
    assert kept[0].id not in ids
    assert {kept[1].id, kept[2].id} == set(ids)


async def test_a_running_attempt_is_never_evicted(configure):
    """Evicting a running attempt would orphan its background task, so the cap
    yields rather than dropping work in flight."""
    with configure(playground_max_attempts_per_user=1):
        running = make_attempt()          # still 'running'
        playground.add(running)
        newer = make_attempt()
        newer.status = "done"
        playground.add(newer)

    ids = {a.id for a in playground.list_for("alice")}
    assert running.id in ids
    assert newer.id in ids


async def test_attempts_are_listed_newest_first_and_per_subject():
    mine_old, mine_new = make_attempt(), make_attempt()
    theirs = make_attempt(subject="bob")
    for attempt in (mine_old, theirs, mine_new):
        playground.add(attempt)
    mine_new.created_at = mine_old.created_at.replace(year=mine_old.created_at.year + 1)

    assert [a.id for a in playground.list_for("alice")] == [mine_new.id, mine_old.id]
    assert [a.id for a in playground.list_for("bob")] == [theirs.id]


async def test_another_subjects_attempt_is_a_404_not_a_403():
    attempt = make_attempt(subject="alice")
    playground.add(attempt)

    assert playground.get(attempt.id, "bob") is None
    with pytest.raises(HTTPException) as exc:
        playground_router._load(attempt.id, "bob")
    assert exc.value.status_code == 404


async def test_missing_attempt_is_a_404():
    with pytest.raises(HTTPException) as exc:
        playground_router._load(uuid.uuid4(), "alice")
    assert exc.value.status_code == 404


# --- Router surface ---------------------------------------------------------

async def test_create_materializes_the_config_like_a_run_does(monkeypatch, configure):
    """A blank field records the environment's value, so the attempt says what it
    actually used rather than a delta against an environment that may change."""
    started: list[PlaygroundAttempt] = []
    monkeypatch.setattr(playground, "start", started.append)

    with configure(agent_base_url="https://env-agent.test", judge_model="env-judge"):
        detail = await playground_router.create_attempt(
            PlaygroundCreate(question="q", config=RunConfig(judge_model="typed-judge")),
            subject="alice",
        )

    attempt = started[0]
    assert attempt.config["judge_model"] == "typed-judge"
    assert attempt.config["agent_base_url"] == "https://env-agent.test"
    assert detail.config.judge_model == "typed-judge"


async def test_credentials_never_come_back_out(monkeypatch):
    """Value-level assertion, not a field-name check: the sentinel must not
    appear anywhere in the serialized payload."""
    started: list[PlaygroundAttempt] = []
    monkeypatch.setattr(playground, "start", started.append)

    detail = await playground_router.create_attempt(
        PlaygroundCreate(
            question="q",
            secrets={"llm_api_key": "sk-SENTINEL-1", "langfuse_secret_key": "lf-SENTINEL-2"},
        ),
        subject="alice",
    )

    assert started[0].secrets["llm_api_key"] == "sk-SENTINEL-1"  # stored inbound
    payload = json.dumps(detail.model_dump(mode="json"))
    assert "SENTINEL" not in payload


async def test_an_override_that_changes_nothing_is_not_sent(monkeypatch):
    """An empty override would make the request body differ from a plain call.

    The agent server treats a present `workspace` as "use this instead of mine",
    so sending an empty one for a question nobody edited would claim an
    experiment that never happened.
    """
    started: list[PlaygroundAttempt] = []
    monkeypatch.setattr(playground, "start", started.append)

    detail = await playground_router.create_attempt(
        PlaygroundCreate(question="q", workspace={"config": {}, "skills": None}),
        subject="alice",
    )

    assert started[0].workspace is None
    assert detail.workspace_overridden is False


async def test_the_baseline_is_read_from_the_agent_not_the_browser(
    monkeypatch, configure
):
    """What counts as "edited" is decided against the agent's own files.

    Trusting a baseline sent by the browser would let a stale tab report a file
    as untouched when the agent has since changed it.
    """
    started: list[PlaygroundAttempt] = []
    monkeypatch.setattr(playground, "start", started.append)

    with configure(workspace_impl="fake"):
        detail = await playground_router.create_attempt(
            PlaygroundCreate(
                question="q",
                workspace={"skills": {"billing/SKILL.md": "# Billing (edited)"}},
            ),
            subject="alice",
        )

    assert started[0].workspace_baseline["billing/SKILL.md"].startswith("# Billing")
    # The other files the fake agent has are absent from the override, i.e.
    # deleted for this call — and reported as such rather than ignored.
    assert "billing/SKILL.md" in detail.edited_skill_files
    assert "reporting/SKILL.md" in detail.edited_skill_files


async def test_an_unreachable_agent_costs_the_summary_not_the_attempt(
    monkeypatch, configure
):
    """The baseline is a nicety; the experiment still has to run without it."""
    started: list[PlaygroundAttempt] = []
    monkeypatch.setattr(playground, "start", started.append)

    class Boom:
        async def get_workspace(self):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(playground_router, "_workspace_client", lambda *a, **k: Boom())
    detail = await playground_router.create_attempt(
        PlaygroundCreate(question="q", workspace={"skills": {"a/SKILL.md": "x"}}),
        subject="alice",
    )

    assert started[0].workspace is not None
    assert detail.workspace_overridden is True


async def test_cancelling_a_finished_attempt_is_a_409():
    attempt = make_attempt()
    attempt.status = "done"
    playground.add(attempt)

    with pytest.raises(HTTPException) as exc:
        await playground_router.cancel_attempt(attempt.id, subject="alice")
    assert exc.value.status_code == 409


async def test_deleting_a_running_attempt_is_a_409():
    attempt = make_attempt()
    playground.add(attempt)

    with pytest.raises(HTTPException) as exc:
        await playground_router.delete_attempt(attempt.id, subject="alice")
    assert exc.value.status_code == 409


async def test_re_diagnose_without_an_expected_process_is_a_409(monkeypatch, seams):
    attempt = make_attempt()
    attempt.trace = Trace(correlation_id="c", spans=[])
    playground.add(attempt)
    monkeypatch.setattr(playground_router, "build_seams", lambda *a, **k: seams)

    with pytest.raises(HTTPException) as exc:
        await playground_router.re_diagnose_attempt(attempt.id, subject="alice")
    assert exc.value.status_code == 409
    assert "expected reasoning" in exc.value.detail


async def test_re_diagnose_without_a_trace_is_a_409(monkeypatch, seams):
    attempt = make_attempt(ground_truth_reasoning="y")
    playground.add(attempt)
    monkeypatch.setattr(playground_router, "build_seams", lambda *a, **k: seams)

    with pytest.raises(HTTPException) as exc:
        await playground_router.re_diagnose_attempt(attempt.id, subject="alice")
    assert exc.value.status_code == 409
    assert "no trace" in exc.value.detail


async def test_re_diagnose_reports_the_models_own_error(monkeypatch):
    seams = Seams(agent=RecordingAgent(), judge=RecordingJudge(), trace=StubTrace(),
                  diagnosis=RecordingDiagnosis(exc=RuntimeError("context length exceeded")))
    attempt = make_attempt(ground_truth_reasoning="y")
    attempt.trace = Trace(correlation_id="c", spans=[])
    playground.add(attempt)
    monkeypatch.setattr(playground_router, "build_seams", lambda *a, **k: seams)

    with pytest.raises(HTTPException) as exc:
        await playground_router.re_diagnose_attempt(attempt.id, subject="alice")
    assert exc.value.status_code == 502
    assert "context length exceeded" in exc.value.detail
    assert "context length exceeded" in attempt.diagnosis_error


async def test_workspace_endpoints_report_misconfiguration_as_503(configure):
    """WORKSPACE_IMPL=real with no agent base URL used to be a 500 — the reason
    has to reach the developer, and it is a configuration problem, not a bug."""
    with configure(workspace_impl="real", agent_base_url=""):
        with pytest.raises(HTTPException) as exc:
            await playground_router.get_workspace(subject="alice")
    assert exc.value.status_code == 503
    assert "AGENT_BASE_URL" in exc.value.detail


async def test_a_broken_workspace_is_a_503_not_an_empty_one(configure, monkeypatch):
    """"No skills" and "the agent server refused us" must not look the same."""
    class Boom:
        async def get_workspace(self):
            raise RuntimeError("agent server returned 500")

    monkeypatch.setattr(playground_router, "_workspace_client", lambda *a, **k: Boom())
    with pytest.raises(HTTPException) as exc:
        await playground_router.get_workspace(subject="alice")
    assert exc.value.status_code == 503
    assert "agent server returned 500" in exc.value.detail


async def test_a_broken_version_check_is_a_503(configure, monkeypatch):
    class Boom:
        async def get_version(self):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(playground_router, "_workspace_client", lambda *a, **k: Boom())
    with pytest.raises(HTTPException) as exc:
        await playground_router.get_workspace_version(subject="alice")
    assert exc.value.status_code == 503
    assert "connection refused" in exc.value.detail


async def test_fake_workspace_is_readable(configure):
    with configure(workspace_impl="fake"):
        ws = await playground_router.get_workspace(subject="alice")

    assert ws.skills["billing/SKILL.md"].startswith("# Billing")
    # A skill is a directory, and the fake has to prove the whole path survives.
    assert "billing/references/refunds.md" in ws.skills
    assert ws.config["agents"]["defaults"]["model"]
    # Secrets are named but not carried, so the UI can show them as hidden
    # rather than letting someone re-add the key by hand and shadow the real one.
    assert "agents.defaults.api_key" in ws.redacted_paths
    assert "api_key" not in ws.config["agents"]["defaults"]


async def test_fake_version_agrees_with_the_snapshot(configure):
    """The staleness check has to be exercised in fake mode, not bypassed."""
    with configure(workspace_impl="fake"):
        ws = await playground_router.get_workspace(subject="alice")
        version = await playground_router.get_workspace_version(subject="alice")

    assert version.version == ws.version != ""


# --- Which agent the workspace is read from ---------------------------------
#
# The playground lets the developer choose the agent they are asking. Everything
# below pins the same rule from a different side: **the workspace comes from the
# agent the question goes to.** Reading the environment instead put agent A's
# skill files in the editor while the attempt ran against agent B — the override
# was built from the wrong text, the "N files edited" count was computed against
# the wrong baseline, and the staleness check compared two servers' versions,
# which can only produce a false answer in one direction or the other.

async def test_the_workspace_is_read_from_the_agent_the_caller_chose(
    configure, monkeypatch
):
    seen: list[tuple] = []

    def spy(base_url=None, timeout_s=None):
        seen.append((base_url, timeout_s))

        class Client:
            async def get_workspace(self):
                return Workspace(
                    version="v-from-b",
                    config={},
                    redacted_paths=[],
                    skills={"b/SKILL.md": "agent B's skill"},
                )

        return Client()

    with configure(workspace_impl="real"):
        monkeypatch.setattr(
            "app.integrations.real.workspace.HttpWorkspaceClient", spy
        )
        ws = await playground_router.get_workspace(
            agent_base_url="http://agent-b:8080",
            agent_timeout_s=42.0,
            subject="alice",
        )

    assert seen == [("http://agent-b:8080", 42.0)]
    assert ws.skills == {"b/SKILL.md": "agent B's skill"}


async def test_the_version_check_asks_the_same_agent(configure, monkeypatch):
    seen: list[tuple] = []

    def spy(base_url=None, timeout_s=None):
        seen.append((base_url, timeout_s))

        class Client:
            async def get_version(self):
                return "v-from-b"

        return Client()

    with configure(workspace_impl="real"):
        monkeypatch.setattr(
            "app.integrations.real.workspace.HttpWorkspaceClient", spy
        )
        out = await playground_router.get_workspace_version(
            agent_base_url="http://agent-b:8080", subject="alice"
        )

    assert out.version == "v-from-b"
    assert seen == [("http://agent-b:8080", None)]


async def test_a_blank_agent_url_still_falls_back_to_the_environment(configure):
    """A single-agent deployment must behave exactly as it did before."""
    with configure(workspace_impl="fake"):
        ws = await playground_router.get_workspace(agent_base_url="", subject="alice")

    assert ws.skills["billing/SKILL.md"].startswith("# Billing")


async def test_the_edit_baseline_comes_from_the_attempts_own_agent(monkeypatch):
    """The "N files edited" label is computed against the agent being asked."""
    started: list[PlaygroundAttempt] = []
    monkeypatch.setattr(playground, "start", started.append)

    seen: list[str | None] = []

    def spy(agent_base_url=None, agent_timeout_s=None):
        seen.append(agent_base_url)

        class Client:
            async def get_workspace(self):
                class WS:
                    skills = {"a/SKILL.md": "from the right agent"}

                return WS()

        return Client()

    monkeypatch.setattr(playground_router, "_workspace_client", spy)
    await playground_router.create_attempt(
        PlaygroundCreate(
            question="q",
            workspace={"skills": {"a/SKILL.md": "edited"}},
            config=RunConfig(agent_base_url="http://agent-b:8080"),
        ),
        subject="alice",
    )

    assert seen == ["http://agent-b:8080"]
    assert started[0].workspace_baseline == {"a/SKILL.md": "from the right agent"}


# --- Trace view states ------------------------------------------------------

async def test_trace_state_is_not_started_before_the_agent_answers():
    attempt = make_attempt()
    assert playground_router._trace_view(attempt).trace_state == "not_started"


async def test_trace_state_is_generating_once_answered_but_not_ingested():
    attempt = make_attempt()
    attempt.agent_response = "the answer"
    attempt.phase = "answered"
    assert playground_router._trace_view(attempt).trace_state == "generating"


async def test_trace_view_carries_the_answer_and_the_expected_answer():
    attempt = make_attempt(ground_truth_response="ACME owed $42,180.")
    attempt.agent_response = "ACME owed $1."
    attempt.verdict = "incorrect"
    attempt.judge_comment = "wrong figure"
    view = playground_router._trace_view(attempt)

    assert view.agent_response == "ACME owed $1."
    assert view.ground_truth_response == "ACME owed $42,180."
    assert view.judge_comment == "wrong figure"


async def test_span_bodies_are_not_truncated_in_the_view(configure):
    """The view path never truncates (§9.19): §6.7's cut is for the diagnosis
    LLM's context window, and applying it here shreds the evidence."""
    long_body = "x" * 5000
    attempt = make_attempt()
    attempt.trace = Trace(correlation_id="c", spans=[
        Span(index=0, tool_name="sql", status="success",
             input=long_body, output=long_body, token_usage={}),
    ])
    with configure(span_body_max_chars=100):
        view = playground_router._trace_view(attempt)

    assert view.spans[0].input == long_body
    assert view.spans[0].output == long_body


async def test_structured_span_bodies_survive_as_objects():
    request = {"messages": [{"role": "user", "content": "hi"}]}
    attempt = make_attempt()
    attempt.trace = Trace(correlation_id="c", spans=[
        Span(index=0, tool_name="gen", status="success", input="{...}", output="{...}",
             token_usage={}, input_json=request, output_json={"role": "assistant"}),
    ])
    view = playground_router._trace_view(attempt)

    assert view.spans[0].input == request
    assert view.spans[0].output == {"role": "assistant"}
