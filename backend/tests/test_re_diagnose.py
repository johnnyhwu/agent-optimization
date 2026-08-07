"""Owner-only manual re-diagnose (§6.12 / §6.16).

This endpoint had no tests. It is also the request path that spends the longest
away from the database — a trace poll followed by a diagnosis LLM call, which
between them can run for minutes — so it is the one whose session handling is
worth pinning: it must not hold a pooled connection while it waits, and it must
still perform its writes afterwards (see app/db.py).

No database: the endpoint runs against a stub session that dispatches on the
queried entity, the same way test_results.py and test_export.py do.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.integrations import Seams
from app.integrations.base import NOT_READY, Span, Trace, TraceFetchError
from app.models import Question, QuestionResult, Run, SpanAnalysis
from app.routers import diagnosis as diagnosis_router

NOW = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)


class StubTraceClient:
    def __init__(self, outcome, session_holder=None):
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


class StubDiagnosisClient:
    model_name = "stub-model"

    def __init__(self, result=None, error=None, session_holder=None):
        self.result = result or {
            "overall_diagnosis": "the sql tool returned the wrong column",
            "caveat": None,
            "suspects": [{
                "span_index": 0, "confidence": "high",
                "reason": "wrong column", "evidence": "select total, not subtotal",
            }],
        }
        self.error = error
        self.commits_when_called: list[int] = []
        self._session_holder = session_holder

    async def diagnose(self, trace, reasoning, verdict):
        session = self._session_holder() if self._session_holder else None
        self.commits_when_called.append(session.commits if session else 0)
        if self.error:
            raise self.error
        return self.result


class StubSession:
    def __init__(self, result=None, run=None, question=None, analysis=None):
        self._objects = {
            "QuestionResult": result, "Run": run, "Question": question,
        }
        self.analysis = analysis
        self.commits = 0
        self.added: list[object] = []
        self.refreshed: list[object] = []

    async def get(self, model, pk):
        obj = self._objects.get(model.__name__)
        return obj if obj is not None and obj.id == pk else None

    async def scalar(self, _statement):
        return self.analysis

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        # `span_analyses.generated_at` is a server default, so it is None on the
        # instance until the database fills it in. Standing in for that is the
        # difference between this stub and one that lets the endpoint return a
        # row the real schema would never produce.
        self.refreshed.append(obj)
        if getattr(obj, "generated_at", None) is None:
            obj.generated_at = NOW


def make_result(verdict="incorrect") -> QuestionResult:
    result = QuestionResult(correlation_id="corr-1", status="done", trace_ready=False)
    result.id = uuid.uuid4()
    result.run_id = uuid.uuid4()
    result.question_pk = uuid.uuid4()
    result.agent_response = "wrong answer"
    result.verdict = verdict
    result.judge_score = 0.2
    result.judge_comment = "does not match"
    result.trace_error = "something from the run"
    result.diagnosis_error = None
    return result


def build(result, *, trace_outcome=None, diagnosis_error=None, analysis=None,
          monkeypatch=None):
    question = Question(question_id="q_1", question="q", ground_truth_response="gt",
                        ground_truth_reasoning="look it up, then sum")
    question.id = result.question_pk
    run = Run(triggered_by="alice", status="completed", config={}, secrets={})
    run.id = result.run_id

    session = StubSession(result=result, run=run, question=question, analysis=analysis)
    trace_client = StubTraceClient(
        trace_outcome if trace_outcome is not None else Trace(
            correlation_id="corr-1",
            spans=[Span(index=0, tool_name="sql", status="success", input="i", output="o")],
        ),
        session_holder=lambda: session,
    )
    diagnosis_client = StubDiagnosisClient(
        error=diagnosis_error, session_holder=lambda: session
    )
    monkeypatch.setattr(
        diagnosis_router, "build_seams",
        lambda config=None, secrets=None: Seams(
            agent=None, judge=None, trace=trace_client, diagnosis=diagnosis_client
        ),
    )
    return session, trace_client, diagnosis_client


async def call(result, session):
    return await diagnosis_router.re_diagnose(
        eval_set_id=uuid.uuid4(), result_id=result.id,
        subject="alice", session=session,
    )


# --- What the endpoint is for -------------------------------------------------


async def test_a_fresh_diagnosis_is_written_and_returned(monkeypatch):
    result = make_result()
    session, _trace, _diag = build(result, monkeypatch=monkeypatch)

    out = await call(result, session)

    assert out.overall_diagnosis == "the sql tool returned the wrong column"
    assert [s.span_index for s in out.suspects] == [0]
    assert out.model_used == "stub-model"
    # A brand-new analysis row, since the stub session reported none existing.
    assert len(session.added) == 1
    assert isinstance(session.added[0], SpanAnalysis)


async def test_an_existing_analysis_is_updated_rather_than_duplicated(monkeypatch):
    """`span_analyses` is unique per question_result — a second row would violate
    the constraint, so this path has to be an update."""
    existing = SpanAnalysis(question_result_id=uuid.uuid4())
    existing.overall_diagnosis = "stale"
    result = make_result()
    session, _trace, _diag = build(result, analysis=existing, monkeypatch=monkeypatch)

    out = await call(result, session)

    assert session.added == []  # nothing new added
    assert existing.overall_diagnosis == "the sql tool returned the wrong column"
    assert out.overall_diagnosis == "the sql tool returned the wrong column"


async def test_a_successful_read_clears_the_runs_stale_trace_error(monkeypatch):
    """The trace was just read successfully, so whatever went wrong during the
    run no longer describes reality."""
    result = make_result()
    session, _trace, _diag = build(result, monkeypatch=monkeypatch)

    await call(result, session)

    assert result.trace_error is None
    assert result.trace_ready is True
    assert result.diagnosis_error is None


async def test_only_incorrect_questions_are_diagnosed(monkeypatch):
    result = make_result(verdict="correct")
    session, trace, _diag = build(result, monkeypatch=monkeypatch)

    with pytest.raises(HTTPException) as exc:
        await call(result, session)

    assert exc.value.status_code == 400
    assert trace.calls == []


async def test_an_unfetchable_trace_is_a_409_not_a_500(monkeypatch):
    result = make_result()
    session, _trace, diag = build(
        result, trace_outcome=NOT_READY, monkeypatch=monkeypatch
    )

    with pytest.raises(HTTPException) as exc:
        await call(result, session)

    assert exc.value.status_code == 409
    # The expensive call is never made when there is nothing to diagnose.
    assert diag.commits_when_called == []


async def test_a_diagnosis_failure_is_recorded_and_reported(monkeypatch):
    """The model's own error is the useful part; a 500 would bury it."""
    result = make_result()
    session, _trace, _diag = build(
        result, diagnosis_error=RuntimeError("model overloaded"),
        monkeypatch=monkeypatch,
    )

    with pytest.raises(HTTPException) as exc:
        await call(result, session)

    assert exc.value.status_code == 502
    assert "model overloaded" in exc.value.detail
    assert "model overloaded" in result.diagnosis_error


async def test_a_broken_trace_store_reports_its_reason(monkeypatch):
    result = make_result()
    session, _trace, _diag = build(
        result, trace_outcome=TraceFetchError("HTTP 401: invalid credentials"),
        monkeypatch=monkeypatch,
    )

    with pytest.raises(HTTPException) as exc:
        await call(result, session)

    assert exc.value.status_code == 409
    assert "invalid credentials" in exc.value.detail


# --- The pooled connection --------------------------------------------------


async def test_no_connection_is_held_across_the_trace_and_llm_calls(monkeypatch):
    """The two slow calls in this handler are a trace poll and a diagnosis LLM
    request — together the longest a request in this system spends waiting. Both
    must happen with the database connection already handed back."""
    result = make_result()
    session, trace, diag = build(result, monkeypatch=monkeypatch)

    await call(result, session)

    assert trace.calls, "precondition: the trace should have been fetched"
    assert all(c >= 1 for c in trace.commits_when_called), (
        "re-diagnose polled the trace store while still holding its connection"
    )
    assert diag.commits_when_called, "precondition: the model should have been called"
    assert all(c >= 1 for c in diag.commits_when_called), (
        "re-diagnose called the diagnosis model while still holding its connection"
    )


async def test_the_writes_still_land_after_the_connection_was_released(monkeypatch):
    """Releasing the connection early is only safe if the session can reacquire
    one — the whole point of the endpoint is the row it writes at the end."""
    result = make_result()
    session, _trace, _diag = build(result, monkeypatch=monkeypatch)

    commits_before = session.commits
    await call(result, session)

    assert session.commits > commits_before
    assert session.refreshed, "the new analysis row was never refreshed"
