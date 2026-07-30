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

from app import cancellation, playground
from app.integrations import Seams
from app.integrations.base import AgentResponse, Span, Trace, Verdict
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

    async def call(self, question, correlation_id, user_id, tags=None, skill_override=None):
        self.calls.append({
            "question": question, "correlation_id": correlation_id,
            "user_id": user_id, "tags": tags, "skill_override": skill_override,
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
        skill_override=None, config={}, secrets={},
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


# --- Skill override ---------------------------------------------------------

async def test_skill_override_reaches_the_agent(seams, monkeypatch):
    from app.integrations.base import SkillOverride

    override = SkillOverride(name="billing", content="# Billing (edited)")
    attempt = make_attempt(skill_override=override)
    await execute(attempt, seams, monkeypatch)

    assert seams.agent.calls[0]["skill_override"] is override
    assert attempt.skill_name == "billing"


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


async def test_skill_endpoints_report_misconfiguration_as_503(configure):
    """SKILL_IMPL=real with no agent base URL used to be a 500 — the reason has
    to reach the developer, and it is a configuration problem, not a bug."""
    with configure(skill_impl="real", agent_base_url=""):
        with pytest.raises(HTTPException) as exc:
            await playground_router.list_skills(subject="alice")
    assert exc.value.status_code == 503
    assert "AGENT_BASE_URL" in exc.value.detail


async def test_a_broken_catalogue_is_a_503_not_an_empty_list(configure, monkeypatch):
    class Boom:
        async def list_skills(self):
            raise RuntimeError("agent server returned 500")

    monkeypatch.setattr(playground_router, "_skill_client", lambda: Boom())
    with pytest.raises(HTTPException) as exc:
        await playground_router.list_skills(subject="alice")
    assert exc.value.status_code == 503
    assert "agent server returned 500" in exc.value.detail


async def test_fake_skill_catalogue_is_readable(configure):
    with configure(skill_impl="fake"):
        skills = await playground_router.list_skills(subject="alice")
        billing = await playground_router.get_skill("billing", subject="alice")

    assert "billing" in [s.name for s in skills]
    assert billing.content.startswith("# Billing")


async def test_unknown_skill_is_a_404(configure):
    with configure(skill_impl="fake"):
        with pytest.raises(HTTPException) as exc:
            await playground_router.get_skill("nope", subject="alice")
    assert exc.value.status_code == 404


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
