"""Folding a trace into one conversation, and keeping it inside a budget.

The bug these tests exist for: every span of an LLM trace is a *whole* request —
tools, system prompt, and all messages so far — so rendering a trajectory span by
span repeated the tool catalogue and the skill once per step and the messages
quadratically. Analyst prompts went out several times the size of the model's
context window, and the first symptom anyone saw was a refusal.

So the first test measures. The rest guard the things the fold must not lose on
its way to being small: what the agent decided, what it finally said, and an
honest account of anything withheld.
"""
from __future__ import annotations

import json

import pytest

from app.integrations.base import Span, Trace
from app.optimizer.analyst import build_user_prompt, format_trajectory_item
from app.optimizer.reflection import build_analyst_items
from app.optimizer.store import ResultRow
from app.optimizer.trajectory import (
    build_trajectory,
    conversation_chars,
    preamble_chars,
    render_trajectory,
    shared_preamble,
    trajectory_chars,
    truncate_trajectory,
)

SYSTEM = "You are a billing agent. " + "The skill says a great deal. " * 200
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_invoices",
            "description": "Search invoices by account. " * 20,
            "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_ledger",
            "description": "Read the ledger. " * 20,
            "parameters": {"type": "object", "properties": {"id": {"type": "string"}}},
        },
    },
]


def _generation(index, messages, output):
    """One span as Langfuse stores an LLM generation: the whole request."""
    payload = {"model": "gpt-x", "tools": TOOLS, "messages": messages}
    return Span(
        index=index,
        tool_name="OpenAI Completion",
        status="success",
        input=json.dumps(payload, ensure_ascii=False, indent=2),
        output=json.dumps(output, ensure_ascii=False, indent=2),
        input_json=payload,
        output_json=output,
    )


def _call(name, args, call_id):
    return {
        "id": call_id, "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def agent_trace(steps=5):
    """A trace in the shape a real agent produces: each span resends the history."""
    history = [{"role": "system", "content": SYSTEM},
               {"role": "user", "content": "How much does ACME owe?"}]
    spans = []
    for i in range(steps):
        if i == steps - 1:
            output = {"role": "assistant", "content": "ACME owes 4,200 EUR."}
        else:
            output = {
                "role": "assistant",
                "content": f"I should look at page {i}.",
                "tool_calls": [_call("search_invoices", {"q": f"acme page {i}"}, f"c{i}")],
            }
        spans.append(_generation(i, list(history), output))
        history.append(output)
        if output.get("tool_calls"):
            history.append({
                "role": "tool", "tool_call_id": f"c{i}",
                "content": f"result page {i}: " + "invoice data. " * 50,
            })
    return Trace(correlation_id="t", spans=spans)


# --- the point of the module ------------------------------------------------


def test_the_tool_catalogue_and_the_system_prompt_appear_exactly_once():
    """Once each, however many steps the agent took.

    They were repeated per span, and the system prompt carries the whole skill,
    so this is most of what made an analyst prompt blow past a context window.
    """
    text = render_trajectory(build_trajectory(agent_trace(steps=6)))

    assert text.count("read_ledger") == 1
    assert text.count(SYSTEM) == 1


def test_no_message_is_shown_twice():
    """Each turn once, even though every span resent the whole history."""
    text = render_trajectory(build_trajectory(agent_trace(steps=6)))

    assert text.count("How much does ACME owe?") == 1
    for i in range(5):
        assert text.count(f"result page {i}:") == 1


def _span_by_span(trace):
    """What the old path sent: every span's whole request, concatenated."""
    return sum(len(s.input or "") + len(s.output or "") for s in trace.spans)


def test_folding_is_several_times_smaller_than_the_spans_it_came_from():
    """The regression lock, in the units the bug was measured in."""
    trace = agent_trace(steps=8)
    folded = len(render_trajectory(build_trajectory(trace)))

    assert folded * 5 < _span_by_span(trace), f"{folded} vs {_span_by_span(trace)}"


def test_the_saving_grows_with_the_length_of_the_trajectory():
    """Linear against quadratic, which is the actual claim.

    Span by span, a trajectory costs O(N²): every one of N spans resends the
    history. Folded it costs O(N). So the ratio between them is itself
    proportional to N — doubling the steps roughly doubles what folding saves,
    which is why long agents were the ones that blew the context window.
    """
    short, long = agent_trace(steps=6), agent_trace(steps=12)
    ratio = lambda t: _span_by_span(t) / len(render_trajectory(build_trajectory(t)))  # noqa: E731

    assert ratio(long) > 1.7 * ratio(short)


def test_the_conversation_keeps_its_order():
    text = render_trajectory(build_trajectory(agent_trace(steps=3)))

    assert text.index("How much does ACME owe?") < text.index("result page 0")
    assert text.index("result page 0") < text.index("result page 1")
    assert text.index("result page 1") < text.index("ACME owes 4,200 EUR.")


# --- dialects ---------------------------------------------------------------


def test_openai_tool_calls_are_rendered_with_their_arguments():
    text = render_trajectory(build_trajectory(agent_trace(steps=2)))

    assert "tool_call search_invoices" in text
    assert "acme page 0" in text


def test_the_anthropic_content_part_shape_is_understood():
    """Tool use inside a content array, and text parts beside it."""
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": [{"type": "text", "text": "How much?"}]},
    ]
    output = {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "Let me look."},
            {"type": "tool_use", "id": "tu1", "name": "read_ledger", "input": {"id": "acme"}},
        ],
    }
    trace = Trace("t", [_generation(0, messages, output)])

    text = render_trajectory(build_trajectory(trace))

    assert "How much?" in text
    assert "Let me look." in text
    assert "tool_call read_ledger" in text
    assert '"id": "acme"' in text


def test_a_span_with_no_structure_is_still_shown():
    """Recognise, never require: an unparsed span is evidence, not noise."""
    span = Span(
        index=0, tool_name="run_sql", status="success",
        input="select * from invoices", output="42 rows",
    )
    trace = Trace("t", [span])

    text = render_trajectory(build_trajectory(trace))

    assert "select * from invoices" in text
    assert "42 rows" in text


def test_a_summarised_history_does_not_lose_the_later_turns():
    """Turns are matched by content, not by position.

    An agent that re-sends a *shorter* history (a compaction, or a second
    session inside one trace) would, under prefix-by-index folding, have its
    later messages read as repeats of the earlier ones and dropped.
    """
    first = _generation(
        0,
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "first question"}],
        {"role": "assistant", "content": "first answer"},
    )
    second = _generation(
        1,
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "second question"}],
        {"role": "assistant", "content": "second answer"},
    )

    text = render_trajectory(build_trajectory(Trace("t", [first, second])))

    for expected in ("first question", "first answer", "second question", "second answer"):
        assert expected in text


# --- what a whole batch shares ----------------------------------------------
#
# The system prompt carries the skill, and on a real agent it is thousands of
# tokens. Every trajectory in a minibatch was answered by the same agent under
# the same candidate skill, so printing it per trajectory spends eight copies of
# it before showing a single tool call.


def _item(key, trace, **over):
    return {
        "id": key, "task_description": "How much does ACME owe?",
        "agent_response": "4,000 EUR", "reference_text": "4,200 EUR",
        "fail_reason": "off by 200", "trajectory": build_trajectory(trace), **over,
    }


def test_a_batch_that_shares_its_setup_is_shown_it_once():
    items = [_item(f"q{i}", agent_trace(steps=4)) for i in range(4)]

    prompt = build_user_prompt(
        "the skill", items, source_type="failure", edit_budget=4, mode="patch",
    )

    assert prompt.count(SYSTEM) == 1
    assert prompt.count("#### Tools Available") == 1
    assert prompt.count("### Trajectory ") == 4
    # And it says so, rather than leaving an analyst to assume the trajectories
    # below it had no system prompt at all.
    assert "What every agent below was set up with" in prompt


def test_hoisting_saves_a_copy_per_extra_trajectory():
    one = build_user_prompt(
        "s", [_item("q0", agent_trace(steps=4))],
        source_type="failure", edit_budget=4, mode="patch",
    )
    eight = build_user_prompt(
        "s", [_item(f"q{i}", agent_trace(steps=4)) for i in range(8)],
        source_type="failure", edit_budget=4, mode="patch",
    )

    # Eight trajectories, but nowhere near eight times the prompt: the shared
    # setup is paid for once.
    assert len(eight) < 8 * len(one)


def test_an_agent_told_something_different_keeps_its_own_setup():
    """Hoisting must not erase the difference. "This one was set up differently"
    is itself a finding, and it is invisible once the batch is assumed uniform."""
    odd = agent_trace(steps=3)
    odd.spans[0].input_json["messages"][0]["content"] = "You are a different agent."

    items = [_item("q0", agent_trace(steps=3)), _item("q1", odd)]
    prompt = build_user_prompt(
        "s", items, source_type="failure", edit_budget=4, mode="patch",
    )

    assert "You are a different agent." in prompt
    assert prompt.count(SYSTEM) == 1  # still there, under its own trajectory
    assert "What every agent below was set up with" not in prompt


def test_one_trajectory_has_nothing_to_hoist():
    assert shared_preamble([build_trajectory(agent_trace(steps=3))]) is None


def test_the_shared_setup_is_charged_once_against_the_budget():
    """Otherwise the batch cuts real evidence to make room for copies of a
    system prompt that is only ever sent once."""
    rows = [_row(f"q{i}", agent_trace(steps=4)) for i in range(4)]
    preamble = preamble_chars(build_trajectory(agent_trace(steps=4)))

    # A budget with room for one copy of the setup and little else. Charged per
    # item, four copies would not fit and every conversation would be shredded.
    items, _ = build_analyst_items(rows, budget_chars=preamble + 4_000)

    kept = sum(conversation_chars(i["trajectory"]) for i in items)
    assert kept > 2_000, kept


# --- truncation -------------------------------------------------------------


def test_a_trajectory_that_fits_is_returned_untouched():
    traj = build_trajectory(agent_trace(steps=3))

    trimmed, ledger = truncate_trajectory(traj, 10_000_000)

    assert ledger == []
    assert render_trajectory(trimmed) == render_trajectory(traj)


def test_tool_results_are_cut_before_anything_else():
    # The budget is over the conversation: the preamble is not cuttable, so
    # counting it here would only mean cutting the conversation to make room for
    # something no cut can reach.
    traj = build_trajectory(agent_trace(steps=5))
    budget = conversation_chars(traj) // 2

    _, ledger = truncate_trajectory(traj, budget, min_keep=100)

    assert ledger, "something should have been cut"
    assert ledger[0]["stage"] == 1  # STAGE_TOOL_RESULT


def test_a_tool_call_is_never_cut_however_small_the_budget():
    """The agent's decision is the one thing a failure analysis cannot do without."""
    traj = build_trajectory(agent_trace(steps=5))

    trimmed, _ = truncate_trajectory(traj, 10, min_keep=50)

    text = render_trajectory(trimmed)
    for i in range(4):
        assert f"acme page {i}" in text


def test_the_final_answer_and_the_system_prompt_survive():
    traj = build_trajectory(agent_trace(steps=5))

    trimmed, _ = truncate_trajectory(traj, 10, min_keep=50)

    text = render_trajectory(trimmed)
    assert "ACME owes 4,200 EUR." in text
    assert SYSTEM in text


def test_the_ledger_says_what_was_cut_and_by_how_much():
    traj = build_trajectory(agent_trace(steps=5))

    trimmed, ledger = truncate_trajectory(traj, conversation_chars(traj) // 3, min_keep=100)

    assert ledger
    for entry in ledger:
        assert entry["after"] < entry["before"]
        assert entry["span_index"] is not None
    assert conversation_chars(trimmed) < conversation_chars(traj)


# --- the budget, end to end -------------------------------------------------


def _row(key, trace):
    return ResultRow(
        item_key=key, correlation_id=key, status="done", verdict="incorrect",
        judge_score=0.0, judge_comment="wrong figure",
        agent_response="ACME owes 4,200 EUR.", trace=trace,
    )


def test_the_batch_is_brought_under_the_budget():
    """The whole reason the budget exists, and the thing it never used to do."""
    rows = [_row(f"q{i}", agent_trace(steps=6)) for i in range(4)]
    budget = 20_000

    items, _ = build_analyst_items(
        rows,
        questions={f"q{i}": "How much does ACME owe?" for i in range(4)},
        ground_truths={f"q{i}": "4,200 EUR" for i in range(4)},
        budget_chars=budget,
    )

    prompt = build_user_prompt(
        "the skill", items, source_type="failure", edit_budget=4, mode="patch",
    )
    body = prompt.split("## Failed Trajectories", 1)[1]
    assert len(body) <= budget * 1.2, len(body)


def test_a_batch_that_cannot_be_cut_far_enough_drops_whole_runs_and_says_so():
    """The last resort, and the ledger entry the page is already looking for."""
    rows = [_row(f"q{i}", agent_trace(steps=6)) for i in range(4)]

    items, ledger = build_analyst_items(
        rows,
        questions={f"q{i}": "How much does ACME owe?" for i in range(4)},
        ground_truths={f"q{i}": "4,200 EUR" for i in range(4)},
        budget_chars=2_000,
    )

    dropped = [
        entry["item_key"]
        for entries in ledger.values() for entry in entries
        if entry["stage"] == "dropped_item"
    ]
    assert dropped, "nothing was recorded as dropped"
    withheld = next(i for i in items if str(i["id"]) in dropped)
    rendered = format_trajectory_item(withheld, 1)
    # The question and the verdict stay; the run itself is named as withheld
    # rather than silently missing.
    assert "How much does ACME owe?" in rendered
    assert "withheld" in rendered


def test_the_analyst_is_never_left_with_an_empty_batch():
    rows = [_row(f"q{i}", agent_trace(steps=6)) for i in range(3)]

    items, _ = build_analyst_items(rows, budget_chars=1)

    assert len(items) >= 1


# --- what the analyst is told about each trajectory -------------------------


def test_the_header_names_the_task_both_answers_and_the_judges_reason():
    item = {
        "id": "q1",
        "task_description": "How much does ACME owe?",
        "agent_response": "4,000 EUR",
        "reference_text": "4,200 EUR",
        "fail_reason": "off by 200",
    }

    text = format_trajectory_item(item, 1)

    assert "#### Task" in text and "How much does ACME owe?" in text
    assert "#### Agent Response" in text and "4,000 EUR" in text
    assert "#### Ground-truth Response" in text and "4,200 EUR" in text
    assert "Failure Reason (from the judge)" in text and "off by 200" in text


@pytest.mark.parametrize("gone", ["Task type", "Hidden Reference"])
def test_the_header_no_longer_carries_upstreams_dead_fields(gone):
    """`Task type` was always blank here, and no analyst prompt says
    "Hidden Reference" — it is the gold answer, and now says so."""
    item = {
        "id": "q1", "task_description": "q", "agent_response": "a",
        "reference_text": "gold", "fail_reason": "no",
    }

    assert gone not in format_trajectory_item(item, 1)


# --- the fold the rollout already paid for ----------------------------------


def test_a_row_that_carries_its_trajectory_is_not_folded_a_second_time():
    """`adapter` folds the trace during the rollout and hangs it on the row.

    The field exists so the reflect stage, which runs minutes later in the same
    step, spends nothing re-deriving what is already in memory. Asserting object
    identity rather than equality is the point: an equal-but-rebuilt trajectory
    is exactly the waste this is here to remove.
    """
    row = _row("q1", agent_trace(steps=3))
    row.trajectory = build_trajectory(row.trace)

    items, _ = build_analyst_items([row], budget_chars=10_000_000)

    assert items[0]["trajectory"] is row.trajectory


def test_a_row_with_only_a_trace_is_still_folded_here():
    """The field is an optimisation, never a requirement.

    Nothing but a live rollout sets it — a resumed step, a replayed trace and
    every test below build rows with a trace alone, and they must keep working.
    """
    row = _row("q1", agent_trace(steps=3))
    assert row.trajectory is None

    items, _ = build_analyst_items([row], budget_chars=10_000_000)

    assert items[0]["trajectory"].turns
