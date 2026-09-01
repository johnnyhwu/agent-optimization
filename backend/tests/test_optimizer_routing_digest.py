"""What a routing analyst is shown instead of trajectories.

A routing decision is made from descriptions alone, *before* the agent acts, so
`(question, gt_skills, skills_read)` is the complete observation of it. The
trajectory that follows is evidence about the answer, not about the choice —
and it is what makes a minibatch expensive enough that the analyst only ever
sees eight questions at a time while rewriting a parameter that governs all of
them.

These tests pin the two renderers that replace it: the confusion matrix the
analyst reads, and the one frozen agent setup printed above it.
"""
from __future__ import annotations

from app.optimizer.routing_digest import (
    DEFAULT_DIGEST_BUDGET_CHARS,
    render_digest,
    system_prompt_view,
)


def item(key, *, tagged, read, question="q?"):
    """One analyst item, in the shape `reflection.analyst_item` produces.

    `read=None` is a question whose trace never landed — not a question the
    agent answered without opening anything, which is `read=[]`. The digest has
    to keep those apart: one is a measurement that did not happen, the other is
    a routing failure.
    """
    tagged = sorted(tagged)
    hard = 0.0 if read is None else float(sorted(read) == tagged)
    return {
        "id": key,
        "hard": hard,
        "task_description": question,
        "gt_skills": tagged,
        "skills_read": None if read is None else sorted(read),
    }


# --- the confusion matrix ---------------------------------------------------


def test_a_skill_gets_a_section_naming_how_many_of_its_questions_reached_it():
    text = render_digest(
        [
            item("a", tagged=["billing"], read=["billing"]),
            item("b", tagged=["billing"], read=["billing"]),
            item("c", tagged=["billing"], read=["reporting"]),
        ],
        targets=["billing"],
    )
    assert "### billing" in text
    # Three tagged, two of which opened it.
    assert "3" in text and "2" in text


def test_a_missed_question_says_what_was_opened_instead():
    text = render_digest(
        [item("c", tagged=["billing"], read=["reporting"], question="扣款明細在哪裡看？")],
        targets=["billing", "reporting"],
    )
    assert "扣款明細在哪裡看？" in text
    assert "reporting" in text


def test_reading_nothing_is_its_own_bucket_and_not_folded_into_reading_the_wrong_skill():
    # A large share of these points at the agent's own setup rather than at any
    # description, and an analyst that cannot separate them will keep editing
    # descriptions to fix something no description controls.
    text = render_digest(
        [
            item("a", tagged=["billing"], read=[]),
            item("b", tagged=["billing"], read=["reporting"]),
        ],
        targets=["billing"],
    )
    assert "nothing" in text.lower()
    lines = text.splitlines()
    nothing = next(i for i, line in enumerate(lines) if "nothing" in line.lower())
    instead = next(i for i, line in enumerate(lines) if "reporting" in line)
    assert nothing != instead


def test_a_question_whose_trace_never_landed_is_reported_as_unmeasured_not_as_a_miss():
    text = render_digest(
        [item("a", tagged=["billing"], read=None, question="no trace here")],
        targets=["billing"],
    )
    assert "no trace here" in text
    assert "not measured" in text.lower()


def test_a_skill_whose_traces_all_went_missing_is_not_reported_as_never_reached():
    # The percentage is over what was *measured*. Reported over what was tagged,
    # a skill whose traces all failed to land reads as "reached by 0 (0%)" — a
    # description condemned on evidence that does not exist, and an invitation
    # to rewrite it. This is the same tri-state `routing_scores` keeps at the
    # gate, held on the page the optimizer reads.
    text = render_digest(
        [item("a", tagged=["billing"], read=None)],
        targets=["billing"],
    )
    assert "0%" not in text
    assert "1 not measured" in text


def test_a_skill_with_some_traces_missing_scores_over_the_rest():
    text = render_digest(
        [
            item("a", tagged=["billing"], read=["billing"]),
            item("b", tagged=["billing"], read=["reporting"]),
            item("c", tagged=["billing"], read=None),
        ],
        targets=["billing"],
    )
    # One of the two that were measured, not one of the three that were tagged.
    assert "1 of the 2 measured (50%)" in text
    assert "1 not measured" in text


def test_questions_that_misfired_into_a_skill_get_their_own_section():
    # The mirror image of a miss, and the one an activation-rate view cannot
    # see: this skill won a question that was not its job.
    text = render_digest(
        [
            item("a", tagged=["reporting"], read=["billing"], question="misfired one"),
        ],
        targets=["billing", "reporting"],
    )
    assert "misfired" in text.lower()
    assert "misfired one" in text


def test_the_header_reports_the_gated_metric_over_measured_questions_only():
    # Two measured, one of them exactly right; the unmeasured one is not a miss
    # and must not be counted as one.
    text = render_digest(
        [
            item("a", tagged=["billing"], read=["billing"]),
            item("b", tagged=["billing"], read=["reporting"]),
            item("c", tagged=["billing"], read=None),
        ],
        targets=["billing"],
    )
    assert "50%" in text


def test_a_skill_with_no_tagged_questions_still_gets_a_section_saying_so():
    # Silence would read as "this one is fine". It is not fine — it has no
    # evidence at all, and editing it is editing on noise.
    text = render_digest(
        [item("a", tagged=["billing"], read=["billing"])],
        targets=["billing", "orphan"],
    )
    assert "### orphan" in text
    assert "no questions" in text.lower()


def test_a_question_tagged_for_two_skills_appears_under_both():
    text = render_digest(
        [item("a", tagged=["billing", "reporting"], read=["billing"], question="spans two")],
        targets=["billing", "reporting"],
    )
    assert text.count("spans two") == 2


def test_an_empty_batch_renders_nothing_rather_than_an_empty_matrix():
    assert render_digest([], targets=["billing"]) == ""


# --- the budget -------------------------------------------------------------


def test_a_batch_over_budget_drops_questions_and_says_how_many():
    items = [
        item(f"q{i}", tagged=["billing"], read=["billing"], question="x" * 200)
        for i in range(200)
    ]
    text = render_digest(items, targets=["billing"], budget_chars=2000)
    assert len(text) <= 2000 * 1.2
    assert "more" in text.lower()
    # The count is still the truth about the batch even when the list is not.
    assert "200" in text


def test_a_batch_that_fits_says_nothing_about_omissions():
    text = render_digest(
        [item("a", tagged=["billing"], read=["billing"], question="short")],
        targets=["billing"],
        budget_chars=DEFAULT_DIGEST_BUDGET_CHARS,
    )
    assert "more not shown" not in text


# --- the frozen agent setup -------------------------------------------------


IDENTICAL = "You are a support agent.\nConsult the skills below.\n"

# A prompt long enough for the similarity threshold to mean something. Two lines
# out of three differing is not the case this is for — a real system prompt runs
# to dozens of lines and varies in one or two of them.
def _boilerplate(varying: str) -> str:
    return "\n".join(
        [
            "You are a support agent for Acme Cloud.",
            varying,
            "Answer the user's question.",
            "Consult the skills below when one applies.",
            "If none applies, answer from your own knowledge.",
            "Never invent an order number.",
            "Quote figures in the account's own currency.",
            "Keep replies under six sentences.",
            "Escalate anything involving a chargeback.",
            "Do not promise a refund date.",
        ]
    ) + "\n"


def test_one_system_prompt_is_printed_verbatim():
    text, divergence = system_prompt_view([IDENTICAL, IDENTICAL])
    assert "You are a support agent." in text
    assert "Consult the skills below." in text
    assert "varies" not in text
    assert divergence.diverged is False


def test_a_line_that_differs_on_every_run_is_marked_rather_than_picked():
    # The ordinary case: an injected timestamp defeats exact-equality hoisting,
    # and printing one run's clock as though it were everyone's is a small lie
    # that costs nothing to avoid.
    prompts = [_boilerplate(f"Today is 2026-08-30 14:0{i} UTC") for i in range(5)]
    text, divergence = system_prompt_view(prompts)
    assert "You are a support agent for Acme Cloud." in text
    assert "Consult the skills below when one applies." in text
    assert "Do not promise a refund date." in text
    assert "varies" in text
    # Every instruction line survives; only the clock is elided, and no single
    # run's clock is presented as though it were everyone's.
    assert "Today is 2026-08-30 14:00 UTC\n" not in text
    assert divergence.diverged is False


def test_only_a_couple_of_samples_are_shown_for_a_varying_line():
    prompts = [_boilerplate(f"Workspace: ws_{i:04d}") for i in range(50)]
    text, _ = system_prompt_view(prompts)
    shown = sum(1 for i in range(50) if f"ws_{i:04d}" in text)
    assert shown <= 2
    assert "50" in text


def test_prompts_that_differ_too_much_are_not_merged_into_one_nobody_ran():
    # Below the threshold there is no shared skeleton worth showing, and a
    # spliced prompt would be a document no question was actually answered
    # under. Print the majority's, labelled as such, and say the rest exist.
    majority = "You are a support agent.\nConsult the skills below.\nBe brief.\n"
    other = "Answer only from your own knowledge.\nNever open a file.\nStay terse.\n"
    text, divergence = system_prompt_view([majority] * 6 + [other] * 4)
    assert "Consult the skills below." in text
    assert "Never open a file." not in text
    assert divergence.diverged is True
    assert divergence.n_variants == 2
    assert "6" in text and "10" in text


def test_divergence_is_reported_with_enough_to_warn_about_it():
    text, divergence = system_prompt_view(["a\nb\nc\n"] * 3 + ["x\ny\nz\n"])
    assert divergence.diverged is True
    assert divergence.n_prompts == 4
    assert divergence.majority_share == 0.75


def test_no_system_prompt_at_all_renders_nothing():
    text, divergence = system_prompt_view([])
    assert text == ""
    assert divergence.diverged is False


def test_a_reordered_tool_catalogue_is_not_a_difference():
    # `shared_preamble` compares tool lists by identity, so a server that
    # returns its catalogue in a different order on each call would look like an
    # agent that was told something different. It was not.
    a = [{"name": "search"}, {"name": "read_file"}]
    b = [{"name": "read_file"}, {"name": "search"}]
    _, divergence = system_prompt_view([IDENTICAL, IDENTICAL], tools=[a, b])
    assert divergence.tools_diverged is False


def test_a_genuinely_different_tool_catalogue_is_a_difference():
    a = [{"name": "search"}]
    b = [{"name": "search"}, {"name": "delete_everything"}]
    _, divergence = system_prompt_view([IDENTICAL, IDENTICAL], tools=[a, b])
    assert divergence.tools_diverged is True


def test_the_default_budget_holds_a_large_workspace():
    # 12 skills and 600 questions is past anything this platform has run, and
    # the point of the number is that it does not need adjusting to survive
    # one: going over the optimizer's context window truncates nothing, the
    # call is refused, and the step loses its gradient entirely.
    targets = [f"skill{i}" for i in range(12)]
    items = [
        item(f"q{i}", tagged=[targets[i % 12]], read=[targets[(i + 1) % 12]],
             question="x" * 400)
        for i in range(600)
    ]
    text = render_digest(items, targets)
    assert len(text) <= DEFAULT_DIGEST_BUDGET_CHARS


def test_whole_sections_go_only_after_every_bucket_is_down_to_one():
    # Depth first, sections last: an analyst shown fewer examples per group can
    # still see every group, and a group it is never told about is a
    # description it edits blind.
    targets = [f"skill{i}" for i in range(6)]
    items = [
        item(f"q{i}", tagged=[targets[i % 6]], read=[targets[i % 6]], question="x" * 200)
        for i in range(120)
    ]
    roomy = render_digest(items, targets, budget_chars=6000)
    assert all(f"### {t}" in roomy for t in targets)
    assert "section(s) omitted" not in roomy

    cramped = render_digest(items, targets, budget_chars=900)
    assert "section(s) omitted" in cramped
