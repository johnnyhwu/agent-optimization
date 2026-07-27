"""Orchestrator failure policy.

These are the cases the fake layer could never produce: fakes never raise, so
before the real seams existed none of these paths were reachable. The rule being
protected is that a run must always finish and always publish a terminal SSE
event — a run stuck in 'running' leaves the UI waiting on a stream nobody closes.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from app import orchestrator
from app.integrations.base import AgentResponse, Trace, Verdict
from app.models import Question, QuestionResult, Run, SpanAnalysis
from app.sse import hub


class StubSession:
    """Just enough AsyncSession for the orchestrator; no database involved."""

    def __init__(self, run: Run, questions: list[Question]) -> None:
        self._run = run
        self._questions = questions
        self.added: list[object] = []
        self.commits = 0
        self.rollbacks = 0

    async def get(self, model, pk):
        if model is Run:
            return self._run if self._run.id == pk else None
        return next((q for q in self._questions if q.id == pk), None)

    async def scalars(self, _statement):
        return _Scalars(self._questions)

    def add(self, obj) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class _Scalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return list(self._items)


def make_question(text="q", reasoning="r", ground_truth="gt") -> Question:
    q = Question(
        question_id="q_1", question=text,
        ground_truth_response=ground_truth, ground_truth_reasoning=reasoning,
    )
    q.id = uuid.uuid4()
    return q


def make_run() -> Run:
    run = Run(triggered_by="alice", status="running")
    run.id = uuid.uuid4()
    run.eval_set_id = uuid.uuid4()
    return run


class Collector:
    """Subscribes to the run's SSE topic and records everything published."""

    def __init__(self, run_id):
        self.run_id = run_id
        self.queue = hub.subscribe(run_id)

    def drain(self) -> list[dict]:
        events = []
        while not self.queue.empty():
            events.append(self.queue.get_nowait())
        return events

    def close(self):
        hub.unsubscribe(self.run_id, self.queue)


@pytest.fixture
def seams(monkeypatch):
    """Install stub seam clients; each test overrides the ones it cares about."""

    class Stubs:
        agent = None
        judge = None
        trace = None
        diagnosis = None

    stubs = Stubs()

    class Agent:
        async def call(self, question, correlation_id):
            return await stubs.agent(question, correlation_id)

    class Judge:
        async def judge(self, question, response, ground_truth):
            return await stubs.judge(question, response, ground_truth)

    class TraceClient:
        async def fetch_trace(self, correlation_id):
            return await stubs.trace(correlation_id)

    class Diagnosis:
        model_name = "stub-model"

        async def diagnose(self, trace, reasoning, verdict):
            return await stubs.diagnosis(trace, reasoning, verdict)

    monkeypatch.setattr(orchestrator, "agent_client", Agent())
    monkeypatch.setattr(orchestrator, "judge_client", Judge())
    monkeypatch.setattr(orchestrator, "trace_client", TraceClient())
    monkeypatch.setattr(orchestrator, "diagnosis_client", Diagnosis())

    async def ok_agent(question, correlation_id):
        return AgentResponse(response="answer", correlation_id=correlation_id, latency_ms=12)

    async def ok_judge(question, response, ground_truth):
        return Verdict(verdict="correct", score=0.9, comment="fine")

    async def no_trace(correlation_id):
        return None

    async def no_diagnosis(trace, reasoning, verdict):
        return {"overall_diagnosis": "d", "suspects": [], "caveat": None}

    stubs.agent = ok_agent
    stubs.judge = ok_judge
    stubs.trace = no_trace
    stubs.diagnosis = no_diagnosis
    return stubs


@pytest.fixture(autouse=True)
def fast_polling(configure):
    with configure(trace_poll_max_attempts=1, trace_poll_backoff_s=[0.0],
                   a2a_max_retries=0, llm_max_retries=0, run_concurrency=1):
        yield


async def test_happy_path_records_agent_answer_and_completes(seams):
    run, questions = make_run(), [make_question()]
    session = StubSession(run, questions)
    collector = Collector(run.id)

    await orchestrator._execute_run(session, run)

    result = next(o for o in session.added if isinstance(o, QuestionResult))
    assert result.agent_response == "answer"
    assert result.agent_latency_ms == 12
    assert result.status == "done"
    assert run.status == "completed"
    assert run.pass_rate == 1.0
    assert any(e["type"] == "run_completed" for e in collector.drain())
    collector.close()


async def test_agent_exception_fails_only_that_question(seams):
    async def boom(question, correlation_id):
        raise ConnectionError("agent unreachable")

    seams.agent = boom
    run, questions = make_run(), [make_question(), make_question()]
    session = StubSession(run, questions)
    collector = Collector(run.id)

    await orchestrator._execute_run(session, run)

    results = [o for o in session.added if isinstance(o, QuestionResult)]
    assert len(results) == 2
    assert all(r.status == "failed" for r in results)
    assert all("agent unreachable" in r.error_message for r in results)
    # Partial completion: the run itself still finishes.
    assert run.status == "completed"
    assert run.pass_rate == 0.0
    assert any(e["type"] == "run_completed" for e in collector.drain())
    collector.close()


async def test_agent_reported_failure_keeps_its_reason(seams):
    async def failed(question, correlation_id):
        return AgentResponse(
            response="", correlation_id=correlation_id, failed=True,
            error="A2A JSON-RPC error: skill not found", latency_ms=8,
        )

    seams.agent = failed
    run, questions = make_run(), [make_question()]
    session = StubSession(run, questions)
    await orchestrator._execute_run(session, run)

    result = next(o for o in session.added if isinstance(o, QuestionResult))
    assert result.status == "failed"
    assert "skill not found" in result.error_message
    assert result.agent_latency_ms == 8


async def test_judge_failure_does_not_silently_pass_the_question(seams):
    async def boom(question, response, ground_truth):
        raise ValueError("judge returned garbage")

    seams.judge = boom
    run, questions = make_run(), [make_question()]
    session = StubSession(run, questions)
    await orchestrator._execute_run(session, run)

    result = next(o for o in session.added if isinstance(o, QuestionResult))
    assert result.status == "failed"
    assert result.verdict is None  # not defaulted to correct
    assert "judge returned garbage" in result.error_message
    assert run.correct_count == 0


async def test_diagnosis_failure_leaves_the_verdict_intact(seams):
    async def wrong(question, response, ground_truth):
        return Verdict(verdict="incorrect", score=0.2, comment="nope")

    async def ready(correlation_id):
        return Trace(correlation_id=correlation_id, spans=[])

    async def boom(trace, reasoning, verdict):
        raise RuntimeError("diagnosis model down")

    seams.judge, seams.trace, seams.diagnosis = wrong, ready, boom
    run, questions = make_run(), [make_question()]
    session = StubSession(run, questions)
    await orchestrator._execute_run(session, run)

    result = next(o for o in session.added if isinstance(o, QuestionResult))
    assert result.status == "done"  # the verdict is the result; diagnosis is extra
    assert result.verdict == "incorrect"
    assert not any(isinstance(o, SpanAnalysis) for o in session.added)
    assert run.status == "completed"


async def test_diagnosis_is_stored_for_incorrect_answers(seams):
    async def wrong(question, response, ground_truth):
        return Verdict(verdict="incorrect", score=0.2, comment="nope")

    async def ready(correlation_id):
        return Trace(correlation_id=correlation_id, spans=[])

    seams.judge, seams.trace = wrong, ready
    run, questions = make_run(), [make_question()]
    session = StubSession(run, questions)
    await orchestrator._execute_run(session, run)

    analysis = next(o for o in session.added if isinstance(o, SpanAnalysis))
    assert analysis.model_used == "stub-model"


async def test_trace_store_error_does_not_fail_the_question(seams):
    async def boom(correlation_id):
        raise ConnectionError("langfuse down")

    seams.trace = boom
    run, questions = make_run(), [make_question()]
    session = StubSession(run, questions)
    await orchestrator._execute_run(session, run)

    result = next(o for o in session.added if isinstance(o, QuestionResult))
    assert result.status == "done"
    assert result.trace_ready is False


async def test_unexpected_error_fails_the_run_instead_of_stranding_it(monkeypatch, seams):
    run = make_run()
    session = StubSession(run, [])

    async def explode(_session, _run):
        raise RuntimeError("catastrophe")

    monkeypatch.setattr(orchestrator, "_execute_run", explode)

    class SessionFactory:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *_):
            return False

    monkeypatch.setattr(orchestrator, "SessionLocal", lambda: SessionFactory())
    collector = Collector(run.id)

    await orchestrator.run_eval(run.id)

    assert run.status == "failed"
    assert "catastrophe" in run.error_message
    assert run.completed_at is not None
    # The stream still gets a terminator, so the UI stops waiting.
    events = collector.drain()
    assert any(e["type"] == "run_completed" and e.get("status") == "failed" for e in events)
    collector.close()


async def test_retries_are_bounded_and_then_give_up(configure):
    attempts = {"n": 0}

    async def always_times_out():
        attempts["n"] += 1
        raise asyncio.TimeoutError()

    with pytest.raises(asyncio.TimeoutError):
        await orchestrator._with_retries(always_times_out, 2, "test call")
    assert attempts["n"] == 3  # initial attempt + 2 retries


async def test_retry_succeeds_on_a_later_attempt():
    attempts = {"n": 0}

    async def flaky():
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise ConnectionError("transient")
        return "ok"

    assert await orchestrator._with_retries(flaky, 2, "test call") == "ok"
    assert attempts["n"] == 2


async def test_concurrency_runs_questions_in_parallel(seams, configure):
    started = []

    async def slow_agent(question, correlation_id):
        started.append(question)
        await asyncio.sleep(0.05)
        return AgentResponse(response="a", correlation_id=correlation_id)

    seams.agent = slow_agent
    run = make_run()
    questions = [make_question(text=f"q{i}") for i in range(4)]
    session = StubSession(run, questions)

    with configure(run_concurrency=4):
        await asyncio.wait_for(orchestrator._execute_run(session, run), timeout=1.0)

    assert run.total_count == 4
    assert run.status == "completed"
