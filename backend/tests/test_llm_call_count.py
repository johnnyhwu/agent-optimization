"""How many model calls one question cost.

The rule is one line, and the whole value of it is in what it refuses to count.
A trace interleaves model calls with the tool invocations they ask for, and only
the first kind is what anybody means by "how many LLM calls" — a question that
made one model call and eleven tool calls is a completely different problem from
one that made eleven of each, and a count that conflated them would send someone
looking at the wrong half of their agent.

Naming cannot decide it. Auto-instrumentation labels every span `OpenAI
Completion` (see `frontend/src/span_label.js`, which exists entirely because of
that), so a rule keyed on the observation's name would count tool spans on one
agent and miss generations on another. Token usage decides it instead: a
generation reports what it spent because it spent something; a tool invocation
has nothing to report.
"""
from __future__ import annotations

from app.integrations.base import Span, Trace
from app.services.trace_view import count_llm_calls


def span(index: int, **usage) -> Span:
    return Span(
        index=index,
        tool_name="OpenAI Completion",
        status="success",
        input="in",
        output="out",
        token_usage=dict(usage),
    )


def test_only_the_spans_that_spent_tokens_are_counted():
    """The case the whole rule exists for, and the one naming cannot settle:
    every span here carries the same generic instrumentation name."""
    trace = Trace("c", [
        span(0, input=120, output=40, total=160),   # a model call
        span(1),                                    # the tool it asked for
        span(2),                                    # and another
        span(3, input=300, output=90, total=390),   # the model, again
    ])

    assert count_llm_calls(trace) == 2


def test_a_partial_usage_report_still_counts():
    """Providers differ in which of the three figures they send, and a
    generation that reported only its prompt tokens is still a generation."""
    assert count_llm_calls(Trace("c", [span(0, input=15)])) == 1
    assert count_llm_calls(Trace("c", [span(0, total=15)])) == 1


def test_a_zero_usage_report_is_not_a_model_call():
    """Some clients stamp a zeroed usage block onto every observation. A call
    that spent nothing did not happen; counting it would put the tool spans
    straight back in."""
    assert count_llm_calls(Trace("c", [span(0, input=0, output=0, total=0)])) == 0


def test_no_trace_is_unknown_rather_than_zero():
    """Two different claims, and only one of them is ever true.

    Langfuse ingestion lags and sometimes fails. `0` would say the agent answered
    without asking anything — a striking fact, and a false one — where `None`
    says nobody was able to count. The column shows nothing for `None`.
    """
    assert count_llm_calls(None) is None
    assert count_llm_calls(Trace("c", [])) is None
