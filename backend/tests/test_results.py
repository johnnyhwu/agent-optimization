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
from app.integrations.base import NOT_READY, Span, Trace, TraceFetchError
from app.models import Question, QuestionResult, Run, SpanAnalysis
from app.routers import results as results_router


class RecordingTraceClient:
    """Counts calls, so "did not touch the trace store" is directly assertable.

    Also snapshots the session's commit count at the moment of each call, which
    is what lets the pool-holding test below assert *ordering* — that the
    database connection was released before the trace store was reached, not
    merely that a commit happened somewhere in the request.
    """

    def __init__(self, outcome=NOT_READY, session_holder=None):
        self.calls: list[str] = []
        self.commits_when_called: list[int] = []
        self.outcome = outcome
        self._session_holder = session_holder

    async def fetch_trace(self, correlation_id):
        self.calls.append(correlation_id)
        session = self._session_holder() if self._session_holder else None
        self.commits_when_called.append(session.commits if session else 0)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class StubSession:
    def __init__(self, **by_type):
        self._objects = by_type
        self.commits = 0

    async def get(self, model, pk):
        obj = self._objects.get(model.__name__)
        return obj if obj is not None and obj.id == pk else None

    async def scalar(self, _statement):
        return self._objects.get("SpanAnalysis")

    async def commit(self):
        self.commits += 1


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
    """Invoke the endpoint with a given result row and trace client.

    The session it built is left on `_call.session` so a test can assert what
    the endpoint did with it, not just what it returned.
    """

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
        _call.session = session
        if isinstance(trace_client, RecordingTraceClient):
            trace_client._session_holder = lambda: session
        return await results_router.get_trace(
            eval_set_id=uuid.uuid4(), result_id=result.id,
            subject="alice", session=session,
        )

    _call.session = None
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


async def test_partial_trace_failure_reads_as_generating_with_the_reason(call_get_trace):
    """One broken Langfuse read path, while another says "not ingested yet".

    The trace is very likely still on its way, so the question must not be shown
    as a dead trace — but the broken endpoint is a real deployment fault and
    stays visible under the "generating" state.
    """
    client = RecordingTraceClient(
        outcome=TraceFetchError(
            "Langfuse partially failed while reading the trace. "
            "[observations_api] HTTP 500: Unknown table expression 'events'",
            partial=True,
        )
    )
    result = make_result(status="done", agent_response="hello", verdict="correct")
    view = await call_get_trace(result, client)

    assert view.trace_state == "generating"
    assert "Unknown table expression" in view.trace_error


# --- The pooled connection must not be held across the trace fetch -----------
#
# `Depends(get_session)` keeps a session — and therefore a pooled connection —
# alive until the response ends. `resolve_trace_spans` polls Langfuse up to
# `trace_poll_max_attempts` times, each attempt able to wait `langfuse_timeout_s`
# per read strategy, so this handler could sit on one of a handful of pooled
# connections for minutes at a time. Enough people opening a trace at once
# exhausted the pool and every unrelated endpoint started failing with
# "QueuePool limit of size 5 overflow 10 reached, connection timed out".
#
# The fix is one `await session.commit()` placed after the last database read
# and before the trace store is touched. These two tests pin both halves of
# that: that it happens at all, and that it happens *first*.


async def test_connection_is_released_before_the_trace_store_is_reached(call_get_trace):
    """The ordering guarantee: commit, then fetch — not the other way round."""
    client = RecordingTraceClient()
    result = make_result(status="done", agent_response="hello", verdict="correct")
    await call_get_trace(result, client)

    assert client.calls, "precondition: this question should have been fetched"
    # Every fetch saw an already-committed session, so no connection was held
    # while waiting on Langfuse.
    assert all(commits >= 1 for commits in client.commits_when_called), (
        "GET .../trace reached the trace store while still holding its database "
        "connection; commit after the last read and before resolve_trace_spans"
    )


async def test_connection_is_released_even_when_no_trace_is_fetched(call_get_trace):
    """The early-exit paths release it too.

    A pending or failed question returns without touching Langfuse, so it was
    never the one exhausting the pool — but leaving its transaction open still
    pins a connection for the rest of the response, and the rule is easier to
    keep when it has no exceptions.
    """
    for status in ("pending", "failed", "cancelled"):
        client = RecordingTraceClient()
        await call_get_trace(make_result(status=status), client)
        assert client.calls == []
        assert call_get_trace.session.commits >= 1, (
            f"a {status} question left its transaction open"
        )
