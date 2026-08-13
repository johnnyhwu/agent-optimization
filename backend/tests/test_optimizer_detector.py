"""Did the agent actually load the skill being optimised?

Everything downstream assumes it did. In `isolated` mode a low activation rate
means optimising the body is pointless — the agent never read it — and the run
should say so rather than spend an hour producing edits nobody will ever
execute. In `routing` mode activation *is* the objective, and it additionally
guards the gate.

Two detectors, because neither alone covers both plausible agent designs:

  * **tool path** — the agent reads `.../skills/billing/SKILL.md` through a tool
    call, so the path is in the trace. This also answers a question a boolean
    cannot: *which* skills it read. "It read `reporting` instead of you" is a far
    stronger signal for the routing analyst than "it did not read you".
  * **content match** — the agent gets the skill injected into its prompt, so
    there is no tool call to find, but the text itself is in the payload. The
    marker is a line **of the skill's own body**; nothing is injected, because
    adding a probe token would put a variable into the very context whose effect
    on the model we are trying to measure.

The third answer matters as much as the two: when neither detector fires and
nothing has established that either *could* fire for this agent, the honest
result is **unknown**, not false. Reporting 0% activation for an agent whose
skill-loading is simply invisible to us would condemn a perfectly good run.
"""
from __future__ import annotations

import json

import pytest

from app.integrations.base import Span, Trace
from app.optimizer.detector import DEFAULT_PATH_PATTERNS, detect_activation

BILLING = {
    "billing/SKILL.md": (
        "---\n"
        "name: billing\n"
        "description: Invoices, balances, refunds and payment status.\n"
        "---\n"
        "# Billing skill\n"
        "1. Identify the customer or order the question is about.\n"
        "2. Query the invoices table with the SQL tool, filtered to that customer.\n"
    ),
    "billing/references/refunds.md": "# Refund rules\nProrated by service days.\n",
}


def span_with(index: int, *, request=None, output=None) -> Span:
    request = request or {"messages": []}
    output = output or {"role": "assistant", "content": "done"}
    return Span(
        index=index, tool_name="t", status="success",
        input=json.dumps(request), output=json.dumps(output),
        input_json=request, output_json=output,
    )


def read_file_span(index: int, path: str) -> Span:
    output = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": "c0", "type": "function",
            "function": {"name": "read_file", "arguments": json.dumps({"path": path})},
        }],
    }
    return span_with(index, output=output)


def detect(trace, *, skill="billing", files=None, detectable=False):
    return detect_activation(
        trace,
        skill_name=skill,
        skill_files=files if files is not None else BILLING,
        path_patterns=DEFAULT_PATH_PATTERNS,
        detectable=detectable,
    )


# --- Tool-path detection ----------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "agent/skills/billing/SKILL.md",
        "/srv/agent/skill/billing/SKILL.md",
        "skills/billing/SKILL.md",
        "/opt/Agent/Skills/Billing/SKILL.md",  # case differs on some deployments
        "workspace\\skills\\billing\\SKILL.md",  # a Windows-shaped path
    ],
)
def test_reading_the_target_skill_counts_as_activation(path):
    """The layout is `<something>/skill(s)/<name>/SKILL.md` in every deployment
    we know of, but the prefix, the case and the separator all vary."""
    result = detect(Trace("c", [read_file_span(0, path)]))

    assert result.activated is True
    assert result.skills_read == ["billing"]
    assert result.hit == "tool_path"


def test_reading_a_different_skill_is_recorded_and_is_not_activation():
    """This is the routing analyst's whole input.

    "The agent read `reporting` when the question was tagged `billing`" says what
    to fix in the description. "It did not read you" does not.
    """
    result = detect(
        Trace("c", [read_file_span(0, "agent/skills/reporting/SKILL.md")]),
        detectable=True,
    )

    assert result.activated is False
    assert result.skills_read == ["reporting"]


def test_several_skills_read_are_all_recorded_in_order():
    trace = Trace("c", [
        read_file_span(0, "skills/reporting/SKILL.md"),
        read_file_span(1, "skills/billing/SKILL.md"),
        read_file_span(2, "skills/reporting/SKILL.md"),
    ])

    result = detect(trace)

    assert result.activated is True
    assert result.skills_read == ["reporting", "billing"], "de-duplicated, first-seen order"


def test_reading_a_reference_file_under_the_skill_also_counts():
    """`references/refunds.md` is part of the skill directory.

    An agent that read the entry point and then a reference has certainly loaded
    the skill; requiring SKILL.md exactly would under-count deep reads.
    """
    result = detect(Trace("c", [read_file_span(0, "skills/billing/references/refunds.md")]))

    assert result.activated is True
    assert result.skills_read == ["billing"]


def test_an_unrelated_file_read_is_not_a_skill():
    """The agent reads plenty of files; only the skill directory pattern counts."""
    trace = Trace("c", [
        read_file_span(0, "data/invoices.csv"),
        read_file_span(1, "/etc/hosts"),
        read_file_span(2, "docs/skills-overview.md"),  # 'skills' but not the layout
    ])

    result = detect(trace, detectable=True)

    assert result.activated is False
    assert result.skills_read == []


# --- Content-match detection ------------------------------------------------


def test_a_body_line_appearing_in_the_payload_counts_as_activation():
    """The agent injected the skill instead of reading it through a tool.

    Nothing is added to the skill to make this work: the marker is a line the
    skill already contains, because a probe token would change the very input
    whose effect we are measuring.
    """
    request = {"messages": [
        {"role": "system", "content":
            "You are an agent.\n2. Query the invoices table with the SQL tool, "
            "filtered to that customer.\n"},
        {"role": "user", "content": "How much did ACME owe?"},
    ]}

    result = detect(Trace("c", [span_with(0, request=request)]))

    assert result.activated is True
    assert result.hit == "content"
    assert result.skills_read == ["billing"]


def test_the_description_appearing_alone_is_not_activation():
    """Offered is not loaded, and routing mode lives on that distinction.

    An agent that lists every skill's description in its system prompt so it can
    choose between them has *offered* this skill. If that counted as activation,
    routing mode would score 100% before it optimised anything and the gate would
    have nothing to measure.
    """
    request = {"messages": [{
        "role": "system",
        "content": (
            "Available skills:\n"
            "- billing: Invoices, balances, refunds and payment status.\n"
            "- reporting: Aggregate reports and trends.\n"
        ),
    }]}

    result = detect(Trace("c", [span_with(0, request=request)]), detectable=True)

    assert result.activated is False, "the description is frontmatter, not body"
    assert result.offered is True, "but routing mode still wants to know it was offered"


def test_content_match_ignores_a_body_too_short_to_be_distinctive():
    """A one-word skill body would match by coincidence in any trace."""
    files = {"billing/SKILL.md": "# B\nDo it.\n"}
    request = {"messages": [{"role": "user", "content": "Do it."}]}

    result = detect(Trace("c", [span_with(0, request=request)]), files=files, detectable=True)

    assert result.activated is False


# --- Unknown is a third answer ---------------------------------------------


def test_neither_detector_firing_is_unknown_not_false_by_default():
    """Reporting 0% activation for an agent we simply cannot observe is a lie.

    Before the pre-flight rollout has shown that *some* detector works for this
    agent, a miss cannot be distinguished from "this agent loads skills in a way
    the trace does not reveal". The run says so instead of condemning itself.
    """
    result = detect(Trace("c", [span_with(0)]), detectable=False)

    assert result.activated is None
    assert result.hit == "none"


def test_once_a_detector_is_known_to_work_a_miss_is_a_real_false():
    """After pre-flight proves the detector fires, absence becomes evidence."""
    result = detect(Trace("c", [span_with(0)]), detectable=True)

    assert result.activated is False
    assert result.hit == "none"


def test_an_empty_trace_is_unknown_rather_than_false():
    """No trace is not the same as a trace showing no skill read.

    Langfuse ingestion can lag or fail; treating that as "the agent ignored the
    skill" would blame the agent for the trace store being unreachable.
    """
    assert detect(Trace("c", []), detectable=True).activated is None


def test_tool_path_is_reported_when_both_detectors_fire():
    """Precedence matters for the UI badge: the stronger evidence wins."""
    request = {"messages": [{
        "role": "system",
        "content": "2. Query the invoices table with the SQL tool, filtered to that customer.",
    }]}
    trace = Trace("c", [
        read_file_span(0, "skills/billing/SKILL.md"),
        span_with(1, request=request),
    ])

    assert detect(trace).hit == "tool_path"
