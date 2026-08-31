"""Which skills a question actually loaded, from the trace it left behind.

One rule, both modes, every skill in the workspace: a skill counts as **read**
when its own body text turns up somewhere the agent was *shown* it — the system
prompt it was set up with, or the result of a tool it called. Nothing is
injected to make that visible; the markers are lines of the skill as sent.

Three things this replaces are worth naming, because each was a way of being
confidently wrong:

  * **Matching tool-call paths.** The old detector looked for `skills/<name>/`
    in a tool call's *arguments*. That reads the agent's vocabulary rather than
    its behaviour: an agent keeping skills under `playbooks/` scored 0% forever,
    and a path in the arguments proves the agent went to read a file, never that
    the file's text arrived.
  * **Searching the whole payload.** Every span's input and output, concatenated.
    A model that quoted one line of the skill back in its own answer was counted
    as having loaded it. Assistant turns are the model's output, not its input,
    and they are excluded here.
  * **The `detectable` flag.** Whether "nothing was seen" meant *no* or *unknown*
    used to depend on a flag the pre-flight set. Now the pre-flight blocks a run
    it cannot see into at all (see `_preflight`), so a trace that exists and
    shows no body is evidence, and only a missing trace is unknown.

Unknown still has to survive as its own answer. Langfuse ingestion lags and
sometimes fails outright; reporting that as "the agent read nothing" would blame
the agent for the trace store, and — once routing accuracy gates on these
numbers — would reject every candidate in a run that had learned nothing.
"""
from __future__ import annotations

from app.integrations.base import Span, Trace
from app.optimizer.detector import (
    build_markers,
    detect_activation,
    read_skills,
    skill_markers,
)
from app.optimizer.trajectory import Trajectory, Turn

BILLING_BODY_LINE = "Quote every figure in the account's own currency, never converted."
REPORTING_BODY_LINE = "Compare each period against the same period one year earlier."

SKILLS = {
    "billing/SKILL.md": (
        "---\n"
        "name: billing\n"
        "description: Invoices, balances and refunds.\n"
        "---\n"
        "# Billing\n"
        f"{BILLING_BODY_LINE}\n"
    ),
    "billing/references/refunds.md": (
        "# Refunds\nRefunds are prorated by the number of service days used.\n"
    ),
    "reporting/SKILL.md": (
        "---\n"
        "name: reporting\n"
        "description: Monthly revenue reports and period comparisons.\n"
        "---\n"
        "# Reporting\n"
        f"{REPORTING_BODY_LINE}\n"
    ),
}


def traj(*, system: str = "", turns: tuple[tuple[str, str], ...] = ()) -> Trajectory:
    return Trajectory(
        system_prompt=system,
        turns=[Turn(role=role, text=text) for role, text in turns],
    )


MARKERS = build_markers(SKILLS)


# --- What counts as a marker ------------------------------------------------


def test_markers_come_from_the_body_not_the_frontmatter():
    """The distinction the whole of routing mode rests on.

    An agent that lists every skill's description in its system prompt so it can
    choose between them has been *offered* this skill. Only body text proves it
    was *loaded*. Collapsing the two scores every skill as read on every
    question, which is 100% before anything has been optimised.
    """
    markers = skill_markers(SKILLS, "billing")

    assert any(BILLING_BODY_LINE in m for m in markers)
    assert not any("Invoices, balances and refunds." in m for m in markers)


def test_reference_files_are_body_too():
    """Reading a reference proves the skill was loaded — skills instruct exactly that."""
    markers = skill_markers(SKILLS, "billing")
    assert any("prorated by the number of service days" in m for m in markers)


def test_reading_only_a_reference_file_still_counts_as_reading_the_skill():
    """The end-to-end half of the rule above.

    A skill that says "for refunds, read references/refunds.md" and an agent
    that does exactly that must not score as an agent that ignored the skill.
    """
    t = traj(turns=(("tool", "Refunds are prorated by the number of service days used."),))
    assert read_skills(t, MARKERS) == {"billing"}


def test_short_lines_are_never_markers():
    """"Do it." would match by coincidence in half the traces ever recorded."""
    files = {"tiny/SKILL.md": "---\nname: tiny\n---\n# T\nDo it.\nStop.\n"}
    assert skill_markers(files, "tiny") == []


def test_a_skill_with_no_markers_can_never_be_read():
    """It must not silently become "read on every question" by matching nothing."""
    files = {"tiny/SKILL.md": "---\nname: tiny\n---\n# T\nDo it.\n"}
    assert read_skills(traj(system="anything at all"), build_markers(files)) == set()


# --- Where the evidence is allowed to come from -----------------------------


def test_a_skill_injected_into_the_system_prompt_counts_as_read():
    """Whole classes of agent never call a tool for this; the text is just there."""
    t = traj(system=f"You are an agent.\n{BILLING_BODY_LINE}\n")
    assert read_skills(t, MARKERS) == {"billing"}


def test_a_skill_returned_by_a_tool_counts_as_read():
    """Which tool it was does not matter — only that the text came back."""
    t = traj(turns=(("assistant", "let me look"), ("tool", f"file contents:\n{BILLING_BODY_LINE}")))
    assert read_skills(t, MARKERS) == {"billing"}


def test_the_model_quoting_the_skill_back_is_not_reading_it():
    """An assistant turn is output, not input. This is the false positive that
    made the old whole-payload search untrustworthy: a good answer that happened
    to restate a rule scored as a load."""
    t = traj(turns=(("assistant", f"As the skill says: {BILLING_BODY_LINE}"),))
    assert read_skills(t, MARKERS) == set()


def test_a_user_turn_counts_because_that_is_where_anthropic_puts_tool_results():
    """The line is drawn at the assistant's output, not at a list of roles.

    A tool result reaches us under `tool` (OpenAI), `user` (Anthropic, as a
    `tool_result` content part) or `span` (the trace store logged the tool as
    its own observation). Enumerating the roles that count means the next
    dialect silently scores 0% — which is how the tool-path detector this
    replaces used to fail. Everything the agent was shown counts; only what it
    wrote does not.
    """
    t = traj(turns=(("user", f"[tool result t1]\n{BILLING_BODY_LINE}"),))
    assert read_skills(t, MARKERS) == {"billing"}


def test_a_span_logged_tool_result_counts():
    """`build_trajectory` gives a non-LLM observation the role `span`."""
    t = traj(turns=(("span", BILLING_BODY_LINE),))
    assert read_skills(t, MARKERS) == {"billing"}


def test_several_skills_can_be_read_in_one_question():
    t = traj(
        system="You are an agent.",
        turns=(
            ("tool", BILLING_BODY_LINE),
            ("tool", REPORTING_BODY_LINE),
        ),
    )
    assert read_skills(t, MARKERS) == {"billing", "reporting"}


def test_reading_nothing_is_an_empty_set_not_unknown():
    """A trace that exists and shows no skill is evidence, not absence of it."""
    t = traj(system="You are an agent.", turns=(("assistant", "I just knew it"),))
    assert read_skills(t, MARKERS) == set()


def test_no_trajectory_is_unknown():
    """Langfuse lags and sometimes fails; that is not the agent reading nothing."""
    assert read_skills(None, MARKERS) is None


def test_an_empty_trajectory_is_unknown():
    assert read_skills(Trajectory(), MARKERS) is None


# --- The per-row view the rollout records -----------------------------------


def _row_activation(trajectory, skill_name="billing"):
    return detect_activation(
        trajectory, skill_name=skill_name, skill_files=SKILLS, workspace_files=SKILLS,
    )


def test_activation_reports_the_target_skill_and_everything_else_read():
    act = _row_activation(traj(turns=(("tool", REPORTING_BODY_LINE),)))

    assert act.activated is False, "billing was not read"
    assert act.skills_read == ["reporting"], "and this is what was read instead"


def test_activation_is_true_when_the_target_was_read():
    act = _row_activation(traj(turns=(("tool", BILLING_BODY_LINE),)))

    assert act.activated is True
    assert act.hit == "tool"


def test_activation_names_where_the_evidence_was_found():
    assert _row_activation(traj(system=BILLING_BODY_LINE)).hit == "system_prompt"
    assert _row_activation(traj(turns=(("tool", BILLING_BODY_LINE),))).hit == "tool"
    assert _row_activation(traj(system="nothing")).hit == "none"


def test_activation_is_unknown_without_a_trajectory():
    act = _row_activation(None)

    assert act.activated is None
    # Not `[]`. See the pair of tests at the end of this file for why the
    # difference decides whether a Langfuse outage counts against a candidate.
    assert act.skills_read is None
    assert act.hit == "none"


def test_a_description_on_the_menu_is_not_a_skill_that_was_read():
    """The menu is not the skill. An agent shown every description in its system
    prompt so it can choose between them has read none of them, and counting the
    menu would score every skill as read on every question — 100% before
    anything has been optimised."""
    act = _row_activation(traj(system="Skills:\n- billing: Invoices, balances and refunds.\n"))

    assert act.activated is False
    assert act.skills_read == []


# --- Reading the whole workspace, not just the target ------------------------


def test_skills_outside_the_target_are_still_detected():
    """Routing accuracy counts what was read *instead*, so every skill sent has
    to be checked — not only the one under optimisation."""
    act = detect_activation(
        traj(turns=(("tool", REPORTING_BODY_LINE),)),
        skill_name="billing", skill_files=SKILLS, workspace_files=SKILLS,
    )
    assert "reporting" in act.skills_read


def test_isolated_can_only_ever_read_the_one_skill_it_was_sent():
    """No special case: isolated sends one skill, so the workspace has one entry
    and the same rule degenerates to "was it read"."""
    only_billing = {k: v for k, v in SKILLS.items() if k.startswith("billing/")}
    act = detect_activation(
        traj(turns=(("tool", REPORTING_BODY_LINE),)),
        skill_name="billing", skill_files=only_billing, workspace_files=only_billing,
    )
    assert act.skills_read == []
    assert act.activated is False


# --- Both message dialects the trace store hands us -------------------------


def test_openai_shaped_tool_results_are_read():
    """`build_trajectory` normalises the dialects; this pins that the detector
    reads what it produces rather than a shape of its own."""
    from app.optimizer.trajectory import build_trajectory

    trace = Trace(
        correlation_id="c",
        spans=[
            Span(
                index=0, tool_name="gen", status="success", input="", output="",
                input_json={
                    "messages": [
                        {"role": "system", "content": "You are an agent."},
                        {"role": "user", "content": "what is the balance?"},
                        {"role": "tool", "content": f"{BILLING_BODY_LINE}"},
                    ]
                },
            )
        ],
    )
    assert read_skills(build_trajectory(trace), MARKERS) == {"billing"}


def test_anthropic_shaped_tool_results_are_read():
    from app.optimizer.trajectory import build_trajectory

    trace = Trace(
        correlation_id="c",
        spans=[
            Span(
                index=0, tool_name="gen", status="success", input="", output="",
                input_json={
                    "messages": [
                        {"role": "system", "content": "You are an agent."},
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "t1",
                                    "content": BILLING_BODY_LINE,
                                }
                            ],
                        },
                    ]
                },
            )
        ],
    )
    assert read_skills(build_trajectory(trace), MARKERS) == {"billing"}


# --- Windows line endings, end to end ---------------------------------------


def test_a_crlf_skill_is_detected_and_its_description_is_not_a_body_marker():
    """The frontmatter fix, seen from the detector's side.

    With `_frontmatter_span` blind to CRLF, this skill had no frontmatter as far
    as the marker split was concerned — so its `description` line became a
    *body* marker, and an agent that merely listed the skill on its menu scored
    as having loaded it.
    """
    crlf = {path: text.replace("\n", "\r\n") for path, text in SKILLS.items()}
    markers = skill_markers(crlf, "billing")

    assert markers, "a CRLF skill still has markers"
    assert not any("Invoices, balances and refunds." in m for m in markers)

    menu_only = traj(system="Skills:\n- billing: Invoices, balances and refunds.\n")
    assert read_skills(menu_only, build_markers(crlf)) == set()


def test_an_unobservable_question_reports_no_skill_list_at_all():
    """`activated=None` and `skills_read=[]` are contradictory, and the empty
    list is the one that gets believed.

    Routing accuracy skips a question whose `skills_read` is None and scores one
    whose list is empty as "opened nothing" — a routing failure. So a trace that
    never landed was being counted against the candidate, which is precisely the
    Langfuse outage this module's tri-state exists to survive: the run rejects
    every step, the chart shows a collapse that never happened, and the message
    says the descriptions got worse.
    """
    act = detect_activation(
        None, skill_name="billing", skill_files=SKILLS, workspace_files=SKILLS,
    )

    assert act.activated is None
    assert act.skills_read is None, "unknown, not 'read nothing'"


def test_a_question_that_read_nothing_reports_an_empty_list():
    """The other half: this one *is* evidence, and must stay distinguishable."""
    act = detect_activation(
        traj(system="You are an agent."),
        skill_name="billing", skill_files=SKILLS, workspace_files=SKILLS,
    )

    assert act.activated is False
    assert act.skills_read == []


# --- where a marker is allowed to come from ---------------------------------
#
# Markers used to be the longest lines of the entry point and every reference
# file *concatenated*. Reference files are where the prose lives, so their lines
# are routinely the longest three — and then no marker came from `SKILL.md` at
# all, and an agent that opened the entry point and nothing else scored as an
# agent that had read no skill. Behind the gate that is a routing accuracy of
# zero for every question tagged for the skill: `score_one` is a set equality,
# the baseline is therefore `0.0` rather than `None` so `_baseline_step` raises
# nothing, and every candidate ties it and is rejected. An hour of rollouts
# drawing a flat line, recorded as ordinary rejections.
#
# The pre-flight cannot see it either: `entry_body_visible` looks only at
# `SKILL.md`, so the probe marker is found and the run is waved through.

PROSE = {
    "billing/SKILL.md": (
        "---\n"
        "name: billing\n"
        "description: Invoices, balances and refunds.\n"
        "---\n"
        "# Billing\n"
        "Quote every figure in the account's own currency.\n"
    ),
    "billing/references/refunds.md": (
        "# Refunds\n"
        "A refund request must be filed within thirty days of the charge date.\n"
        "A refund is prorated by the number of service days actually consumed.\n"
        "A refund to a closed card is issued as account credit instead of cash.\n"
    ),
}


def test_the_entry_point_always_keeps_a_marker_of_its_own():
    """However much longer the prose in a reference file is."""
    markers = skill_markers(PROSE, "billing")

    assert any("Quote every figure in the account's own currency." in m for m in markers)


def test_an_agent_that_opens_only_the_entry_point_has_read_the_skill():
    """The reviewer's reproduction: correct routing, scored as no skill at all."""
    entry_only = traj(turns=(("tool", PROSE["billing/SKILL.md"]),))

    assert read_skills(entry_only, build_markers(PROSE)) == {"billing"}


def test_a_reference_file_keeps_markers_of_its_own_too():
    """The other half of the rule: reading a reference still proves a load."""
    one_reference = traj(turns=(("tool", "A refund to a closed card is issued as account credit instead of cash."),))

    assert read_skills(one_reference, build_markers(PROSE)) == {"billing"}


# --- a line two skills share is evidence for neither -------------------------


SHARED_LINE = "Escalate to a human whenever the account is flagged for review."
BOILERPLATE = {
    "billing/SKILL.md": (
        "---\nname: billing\ndescription: Invoices and balances.\n---\n"
        f"# Billing\n{SHARED_LINE}\nQuote every figure in the account's own currency.\n"
    ),
    "reporting/SKILL.md": (
        "---\nname: reporting\ndescription: Monthly reports.\n---\n"
        f"# Reporting\n{SHARED_LINE}\nCompare each period against the same period one year earlier.\n"
    ),
}


def test_a_line_shared_by_two_skills_is_never_a_marker():
    """`hard` is a set equality, so one false positive zeroes the question.

    Two skills that share a boilerplate line would both read as opened on any
    trace carrying it — and a question tagged for one of them then scores zero
    for having "opened" the other.
    """
    markers = build_markers(BOILERPLATE)

    assert not any(SHARED_LINE in m for m in markers["billing"])
    assert not any(SHARED_LINE in m for m in markers["reporting"])


def test_the_shared_line_alone_reads_as_no_skill_opened():
    assert read_skills(traj(turns=(("tool", SHARED_LINE),)), build_markers(BOILERPLATE)) == set()


def test_each_skill_is_still_detected_by_a_line_of_its_own():
    """Dropping the shared line must not cost the skills their own evidence."""
    markers = build_markers(BOILERPLATE)
    billing = traj(turns=(("tool", "Quote every figure in the account's own currency."),))

    assert read_skills(billing, markers) == {"billing"}
