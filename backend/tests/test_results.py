"""The trace view's state machine.

The rule under test: `GET .../trace` decides what to do from how far the question
actually got, not just from its status. A question the agent hasn't been asked
yet has no trace to fetch, and reaching for one anyway was the bug behind
"a brand-new run shows the previous run's Langfuse error" — the error was fresh,
identical, and produced by a request that should never have been made.

No database: the router is exercised directly against a stub session, the same
way test_orchestrator.py does.
"""
from __future__ import annotations

import uuid

import pytest

from app.config import settings
from app.integrations import Seams
from app.integrations.base import NOT_READY, Span, Trace
from app.models import Question, QuestionResult, Run, SpanAnalysis
from app.routers import results as results_router


class RecordingTraceClient:
    """Counts calls, so "did not touch the trace store" is directly assertable."""

    def __init__(self, outcome=NOT_READY):
        self.calls: list[str] = []
        self.outcome = outcome

    async def fetch_trace(self, correlation_id):
        self.calls.append(correlation_id)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class StubSession:
    def __init__(self, **by_type):
        self._objects = by_type

    async def get(self, model, pk):
        obj = self._objects.get(model.__name__)
        return obj if obj is not None and obj.id == pk else None

    async def scalar(self, _statement):
        return self._objects.get("SpanAnalysis")


def make_result(**kwargs) -> QuestionResult:
    result = QuestionResult(
        correlation_id="corr-1", status=kwargs.pop("status", "pending"),
        trace_ready=kwargs.pop("trace_ready", False),
    )
    result.id = uuid.uuid4()
    result.run_id = uuid.uuid4()
    result.question_pk = uuid.uuid4()
    result.agent_response = kwargs.pop("agent_response", None)
    result.verdict = kwargs.pop("verdict", None)
    result.judge_score = None
    result.judge_comment = None
    result.error_message = None
    result.trace_error = kwargs.pop("trace_error", None)
    result.diagnosis_error = None
    return result


def make_question(result: QuestionResult) -> Question:
    q = Question(
        question_id="q_1", question="q", ground_truth_response="gt",
        ground_truth_reasoning="r",
    )
    q.id = result.question_pk
    return q


def make_run(result: QuestionResult) -> Run:
    run = Run(triggered_by="alice", status="running", config={}, secrets={})
    run.id = result.run_id
    return run


@pytest.fixture
def call_get_trace(monkeypatch):
    """Invoke the endpoint with a given result row and trace client."""

    async def _call(result: QuestionResult, trace_client, analysis: SpanAnalysis | None = None):
        monkeypatch.setattr(
            results_router,
            "build_seams",
            lambda config=None, secrets=None: Seams(
                agent=None, judge=None, trace=trace_client, diagnosis=None
            ),
        )
        session = StubSession(
            QuestionResult=result,
            Question=make_question(result),
            Run=make_run(result),
            SpanAnalysis=analysis,
        )
        return await results_router.get_trace(
            eval_set_id=uuid.uuid4(), result_id=result.id,
            subject="alice", session=session,
        )

    return _call


async def test_pending_question_never_touches_the_trace_store(call_get_trace):
    """The regression test for the duplicated-error report.

    A pending question is one the agent hasn't answered. Fetching a trace for it
    can only fail, and on a broken Langfuse it fails with exactly the message the
    last run left behind — which is why it read as a stale error being replayed.
    """
    client = RecordingTraceClient(outcome=RuntimeError("SQL Error: Unknown table expression 'events'"))
    view = await call_get_trace(make_result(status="pending"), client)

    assert view.trace_state == "not_started"
    assert view.trace_error is None
    assert client.calls == []  # not one request


async def test_pending_question_ignores_a_previous_runs_recorded_error(call_get_trace):
    """Even a trace_error left on the row must not surface before the agent ran."""
    result = make_result(status="pending", trace_error="Langfuse blew up last time")
    view = await call_get_trace(result, RecordingTraceClient())
    assert view.trace_state == "not_started"
    assert view.trace_error is None


async def test_answered_question_does_fetch(call_get_trace):
    """Once the agent has answered, a trace can legitimately exist — waiting on
    ingestion is the whole point of §6.12."""
    client = RecordingTraceClient()
    result = make_result(status="done", agent_response="hello", verdict="correct")
    view = await call_get_trace(result, client)

    assert view.trace_state == "generating"
    assert client.calls  # polled, as it should


async def test_ready_trace_returns_spans(call_get_trace):
    client = RecordingTraceClient(
        outcome=Trace(
            correlation_id="corr-1",
            spans=[Span(index=0, tool_name="sql", status="success", input="i", output="o")],
        )
    )
    result = make_result(status="done", agent_response="hello", verdict="incorrect")
    view = await call_get_trace(result, client)

    assert view.trace_state == "ready"
    assert [s.tool_name for s in view.spans] == ["sql"]


async def test_span_bodies_are_not_truncated_on_the_view_path(call_get_trace):
    """§6.7's cut belongs to the diagnosis prompt, where a context window is the
    constraint. Applying it here shredded the evidence the span view exists to
    show — and left structured payloads unparseable for the UI."""
    huge = "x" * (settings.span_body_max_chars * 5)
    client = RecordingTraceClient(
        outcome=Trace(
            correlation_id="corr-1",
            spans=[Span(index=0, tool_name="sql", status="success", input="i", output=huge)],
        )
    )
    result = make_result(status="done", agent_response="hello", verdict="incorrect")
    view = await call_get_trace(result, client)

    assert view.spans[0].output == huge


async def test_structured_span_body_is_served_as_an_object(call_get_trace):
    """So the UI can render an LLM call per message rather than a JSON dump."""
    request = {"tools": [], "messages": [{"role": "user", "content": "hi"}]}
    client = RecordingTraceClient(
        outcome=Trace(
            correlation_id="corr-1",
            spans=[Span(
                index=0, tool_name="generate", status="success",
                input="{flattened text}", output="o",
                input_json=request, output_json={"role": "assistant", "content": "hey"},
            )],
        )
    )
    result = make_result(status="done", agent_response="hello", verdict="incorrect")
    view = await call_get_trace(result, client)

    assert view.spans[0].input == request
    assert view.spans[0].output == {"role": "assistant", "content": "hey"}


async def test_failed_question_reports_no_trace_without_fetching(call_get_trace):
    client = RecordingTraceClient()
    view = await call_get_trace(make_result(status="failed"), client)
    assert view.trace_state == "no_trace"
    assert client.calls == []


async def test_trace_store_failure_is_reported_as_error_not_generating(call_get_trace):
    """A misconfigured trace store must not masquerade as async ingestion."""
    client = RecordingTraceClient(outcome=RuntimeError("HTTP 401: invalid credentials"))
    result = make_result(status="done", agent_response="hello", verdict="correct")
    view = await call_get_trace(result, client)

    assert view.trace_state == "error"
    assert "invalid credentials" in view.trace_error
