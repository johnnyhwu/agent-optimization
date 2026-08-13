"""Structure-aware trace truncation: cut the data, never the decisions.

The reflect stage sends a whole minibatch of traces to one analyst call, so
something has to give when they do not fit. *What* gives decides whether the
optimizer can see anything useful:

  * A **tool call** — its name and its arguments — is the agent's decision. It
    is the only place "it queried the wrong table" is visible at all; the final
    answer looks identical whether the right table was queried or the wrong one.
    Cutting it turns a diagnosable failure into an undiagnosable one.
  * A **tool result** is data. Losing its middle costs nothing the analyst was
    going to use.

So the cascade is ordered, and — the part that is easy to get wrong — it is
**driven by measurement, not applied unconditionally**. A trace that fits is
passed through untouched, character for character. Truncation is a last resort
that escalates one stage at a time and stops the moment the payload is under
budget.

`truncate_body` is deliberately left alone and reused for the elision itself, so
the marker a developer sees in a reflect prompt is the same one they see in a
diagnosis prompt. Its own behaviour is covered by `test_judge_and_diagnosis.py`
and `test_playground.py`, which must keep passing unchanged.
"""
from __future__ import annotations

import json

import pytest

from app.integrations.base import Span, Trace
from app.services.truncation import (
    allocate_budget,
    trace_chars,
    truncate_body,
    truncate_trace,
)

TOOL_ARGS = json.dumps({"table": "invoices", "region": "EMEA", "period": "2026Q2"})
SKILL_TEXT = "# Billing skill\n1. Identify the customer.\n2. Query invoices.\n"


def tool_call_span(index: int, *, result: str, tool: str = "sql_query") -> Span:
    """A span whose request carries the conversation so far and whose output is a call."""
    request = {
        "model": "m",
        "tools": [{"type": "function", "function": {"name": tool}}],
        "messages": [
            {"role": "system", "content": SKILL_TEXT},
            {"role": "user", "content": "How much did ACME owe?"},
            {"role": "tool", "tool_call_id": "call_00", "name": tool, "content": result},
        ],
    }
    output = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": f"call_{index:02d}",
            "type": "function",
            "function": {"name": tool, "arguments": TOOL_ARGS},
        }],
    }
    return Span(
        index=index, tool_name=tool, status="success",
        input=json.dumps(request), output=json.dumps(output),
        input_json=request, output_json=output,
    )


def final_span(index: int, *, answer: str = "ACME owed $42,180.00.") -> Span:
    request = {
        "model": "m",
        "messages": [
            {"role": "system", "content": SKILL_TEXT},
            {"role": "user", "content": "How much did ACME owe?"},
        ],
    }
    output = {"role": "assistant", "content": answer}
    return Span(
        index=index, tool_name="final", status="success",
        input=json.dumps(request), output=json.dumps(output),
        input_json=request, output_json=output,
    )


def trace_of(*spans: Span) -> Trace:
    return Trace(correlation_id="c1", spans=list(spans))


def big(n: int = 4000) -> str:
    return "row-data " * n


def all_text(trace: Trace) -> str:
    return "\n".join(f"{s.input}\n{s.output}" for s in trace.spans)


# --- Measurement drives everything -----------------------------------------


def test_a_trace_that_fits_is_returned_character_for_character():
    """Truncation is a last resort, not a formatting pass.

    Most traces fit. If the cascade ran unconditionally, every reflect prompt
    would arrive with elision markers in it and the analyst would be reasoning
    about mutilated evidence for no reason at all.
    """
    trace = trace_of(tool_call_span(0, result="small result"), final_span(1))

    out, ledger = truncate_trace(trace, budget_chars=100_000)

    assert ledger == []
    assert [s.input for s in out.spans] == [s.input for s in trace.spans]
    assert [s.output for s in out.spans] == [s.output for s in trace.spans]


def test_cutting_stops_as_soon_as_the_payload_is_under_budget():
    """Each stage is entered only if the one before it was not enough.

    A cascade that ran to completion every time would strip the user's question
    and the intermediate assistant turns out of a trace that only needed one
    oversize tool result trimmed.
    """
    trace = trace_of(
        tool_call_span(0, result=big()),
        tool_call_span(1, result="tiny"),
        final_span(2),
    )
    budget = trace_chars(trace) // 2

    out, ledger = truncate_trace(trace, budget_chars=budget)

    assert trace_chars(out) <= budget
    assert {entry["stage"] for entry in ledger} == {1}, "only stage 1 was needed"
    assert "How much did ACME owe?" in all_text(out), "the question survives"


# --- What may never be cut --------------------------------------------------


def test_tool_call_arguments_survive_an_absurdly_small_budget():
    """The arguments are the evidence of *what the agent decided to do*.

    This is the single most important guarantee in this module. "It queried the
    wrong table" is invisible in the final answer and invisible in the tool
    result; it lives here and nowhere else.
    """
    trace = trace_of(tool_call_span(0, result=big()), final_span(1))

    out, _ = truncate_trace(trace, budget_chars=50)

    # Asserted against the structure, not the serialized text: `arguments` is
    # itself JSON nested inside JSON, so it appears escaped in the body and a
    # substring check would pass or fail for reasons unrelated to truncation.
    call = out.spans[0].output_json["tool_calls"][0]["function"]
    assert call["arguments"] == TOOL_ARGS, "the agent's decision must survive intact"
    assert call["name"] == "sql_query"
    assert json.loads(out.spans[0].output) == out.spans[0].output_json, (
        "the serialized body and the structured one must not drift apart"
    )


def test_the_final_answer_survives_an_absurdly_small_budget():
    """The judge's verdict is about this text; the analyst has to see it."""
    trace = trace_of(tool_call_span(0, result=big()), final_span(1))

    out, _ = truncate_trace(trace, budget_chars=50)

    assert "ACME owed $42,180.00." in all_text(out)


def test_the_first_system_message_survives_because_it_carries_the_skill():
    """The skill under optimisation is in there.

    Cutting it would ask the analyst to propose edits to a document it was not
    shown — and, worse, the content-match activation detector reads the same
    payload, so a cut system message would also read as "the agent never loaded
    the skill".
    """
    trace = trace_of(tool_call_span(0, result=big()), final_span(1))

    out, _ = truncate_trace(trace, budget_chars=50)

    assert "# Billing skill" in all_text(out)


def test_every_span_is_still_listed_however_small_the_budget():
    """§6.7's rule, unchanged: cut the BODY, never the SPAN.

    Root causes are often early and symptoms late, so a trace missing spans
    invites a diagnosis of a failure that never happened. The same reasoning
    applies to reflection, which is proposing skill edits from the same evidence.
    """
    trace = trace_of(
        tool_call_span(0, result=big()),
        tool_call_span(1, result=big()),
        tool_call_span(2, result=big()),
        final_span(3),
    )

    out, _ = truncate_trace(trace, budget_chars=10)

    assert [s.index for s in out.spans] == [0, 1, 2, 3]
    assert [s.tool_name for s in out.spans] == [s.tool_name for s in trace.spans]


# --- Stage order ------------------------------------------------------------


def test_tool_results_are_cut_before_conversation_turns():
    """Data before decisions, always.

    With one huge tool result and one huge user message, the tool result must be
    the one that gives — even though cutting either would satisfy the budget.
    """
    request = {
        "model": "m",
        "messages": [
            {"role": "system", "content": SKILL_TEXT},
            {"role": "user", "content": "Q: " + big(500)},
            {"role": "tool", "tool_call_id": "c", "name": "sql_query", "content": big(500)},
        ],
    }
    span = Span(
        index=0, tool_name="sql_query", status="success",
        input=json.dumps(request), output=json.dumps({"role": "assistant", "content": "ok"}),
        input_json=request, output_json={"role": "assistant", "content": "ok"},
    )
    trace = trace_of(span, final_span(1))
    budget = trace_chars(trace) - 3000

    out, ledger = truncate_trace(trace, budget_chars=budget)

    messages = out.spans[0].input_json["messages"]
    user, tool_result = messages[1], messages[2]

    assert "truncated" in tool_result["content"], "the tool result should have been cut"
    assert "truncated" not in user["content"], (
        "the user's question was cut while a tool result was still whole"
    )
    assert all(entry["stage"] == 1 for entry in ledger), (
        f"a later stage ran before stage 1 had finished: {ledger}"
    )


def test_the_biggest_tool_result_is_cut_first():
    """Cutting the small one first would need several cuts to free the same room."""
    small = "s" * 500
    huge = "h" * 40_000
    trace = trace_of(
        tool_call_span(0, result=small),
        tool_call_span(1, result=huge),
        final_span(2),
    )
    budget = trace_chars(trace) - 30_000

    out, ledger = truncate_trace(trace, budget_chars=budget)

    assert len(ledger) == 1
    assert ledger[0]["span_index"] == 1


# --- The ledger -------------------------------------------------------------


def test_the_ledger_records_every_cut_with_its_sizes():
    """Part 1 renders this so a developer can see what the analyst was denied.

    Without it, "why did the optimizer propose something so obviously wrong?"
    has no answer — the prompt on screen would look complete.
    """
    trace = trace_of(tool_call_span(0, result=big()), final_span(1))

    out, ledger = truncate_trace(trace, budget_chars=trace_chars(trace) // 4)

    assert ledger, "a cut happened, so it must be recorded"
    entry = ledger[0]
    assert set(entry) >= {"span_index", "field", "before", "after", "stage"}
    assert entry["before"] > entry["after"] > 0
    assert entry["span_index"] == 0


def test_the_ledger_totals_match_the_payload_that_was_produced():
    """A ledger that disagreed with the payload would be worse than none."""
    trace = trace_of(
        tool_call_span(0, result=big()),
        tool_call_span(1, result=big()),
        final_span(2),
    )

    out, ledger = truncate_trace(trace, budget_chars=trace_chars(trace) // 3)

    saved = sum(entry["before"] - entry["after"] for entry in ledger)
    assert trace_chars(trace) - trace_chars(out) == pytest.approx(saved, abs=len(ledger) * 80)


# --- Budget allocation across a minibatch -----------------------------------


def test_budget_is_split_evenly_when_every_trace_is_the_same_size():
    assert allocate_budget([1000, 1000, 1000], 3000) == [1000, 1000, 1000]


def test_a_small_trace_gives_its_unused_share_to_a_large_one():
    """Otherwise one giant trace is mangled while seven small ones waste room.

    The equal split is only a starting point: what matters is that the whole
    budget gets used, and that a minibatch containing one enormous trace does not
    truncate that trace to a stub while the others are nowhere near their share.
    """
    shares = allocate_budget([100, 100, 10_000], 3_000)

    assert shares[0] == 100 and shares[1] == 100, "a trace under its share keeps its size"
    assert shares[2] == 2_800, "the unused remainder goes to the trace that needs it"
    assert sum(shares) <= 3_000


def test_allocation_never_exceeds_the_total_budget():
    """The whole point of the total is that one analyst call has to fit in it."""
    for sizes in ([50_000, 50_000], [1, 1, 1], [10, 10_000, 20]):
        assert sum(allocate_budget(sizes, 1_000)) <= 1_000


def test_allocation_handles_no_traces():
    assert allocate_budget([], 1_000) == []


# --- The old function is untouched -----------------------------------------


def test_truncate_body_still_behaves_exactly_as_the_diagnosis_path_expects():
    """`truncate_body` is shared with diagnosis and its behaviour is frozen.

    Restated here rather than left implicit: this module gained a function, it
    did not change one. The diagnosis prompt's elision marker is the same string
    a reflect prompt shows, which is why the two paths can share a reader.
    """
    short = "abc"
    assert truncate_body(short, 100) == (short, False)

    text, cut = truncate_body("x" * 500, 100)
    assert cut is True
    assert len(text) < 500
    assert "truncated" in text
