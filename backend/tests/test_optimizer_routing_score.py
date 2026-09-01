"""Routing accuracy: did the agent reach for the skills the question belongs to?

`routing` mode optimises the description that decides *when* an agent opens a
skill. What it was gated on until now was the target skill's activation rate not
falling, which is a proxy with two holes:

  * it watches **one** skill, so a description widened until it wins every
    question scores perfectly while starving every other skill on the agent;
  * it is a **rate over a rollout**, so it cannot say whether the *right*
    question was the one that opened the skill.

Every question already carries the skill tags it belongs to, so the thing the
mode is actually trying to improve is directly measurable, per question:

    hard = 1 when the skills read are exactly the skills tagged
    soft = F1 between the two sets

Both go through the gate machinery unchanged — `select_gate_score` projects
`(hard, soft)` onto whichever metric the run configured — so this adds numbers,
not a second gate. `hard` alone is deliberately harsh (a strict set match over a
few dozen validation questions moves in large steps and can sit at zero early),
which is exactly why `soft` and `mixed` are worth having, and why the wizard now
offers the choice.

The judge keeps running and its accuracy keeps being recorded. A routing run
that fixed the routing and did not improve the answers is the finding that says
to go and run an isolated one next, and it is invisible if only the gating
number survives.
"""
from __future__ import annotations

import pytest

from app.optimizer.adapter import score_rollout
from app.optimizer.routing import routing_scores
from app.optimizer.store import Item, ResultRow


def item(key, *gt):
    return Item(
        item_key=key, question="q", ground_truth_response="a",
        ground_truth_reasoning="r", gt_skills=tuple(gt),
    )


def row(key, read, *, verdict="correct"):
    return ResultRow(
        item_key=key, correlation_id="c", status="done", verdict=verdict,
        judge_score=1.0 if verdict == "correct" else 0.0,
        skills_read=None if read is None else sorted(read),
    )


# --- One question at a time --------------------------------------------------


def test_reading_exactly_the_tagged_skill_is_a_hit():
    hard, soft = routing_scores([row("k", {"billing"})], [item("k", "billing")])
    assert (hard, soft) == (1.0, 1.0)


def test_reading_the_wrong_skill_is_a_miss():
    hard, soft = routing_scores([row("k", {"reporting"})], [item("k", "billing")])
    assert hard == 0.0
    assert soft == 0.0


def test_reading_nothing_is_a_miss():
    hard, soft = routing_scores([row("k", set())], [item("k", "billing")])
    assert (hard, soft) == (0.0, 0.0)


def test_both_tagged_skills_must_be_read():
    hard, _ = routing_scores([row("k", {"billing"})], [item("k", "billing", "reporting")])
    assert hard == 0.0, "half the job is not the job"


def test_reading_both_tagged_skills_is_a_hit():
    hard, soft = routing_scores(
        [row("k", {"billing", "reporting"})], [item("k", "billing", "reporting")]
    )
    assert (hard, soft) == (1.0, 1.0)


def test_reading_an_extra_skill_is_a_miss():
    """Opening a skill that was not this question's job is a routing error.

    It is the failure a widened description produces, and the reason `hard` is a
    set *equality* and not "were the tagged ones among them".
    """
    hard, _ = routing_scores([row("k", {"billing", "shipping"})], [item("k", "billing")])
    assert hard == 0.0


def test_soft_gives_partial_credit_where_hard_cannot():
    """The reason both numbers exist.

    Strict equality over a few dozen validation questions moves a question at a
    time and can sit flat at zero for the first several steps, which leaves the
    gate nothing to compare. F1 separates "read one of the two right ones" from
    "read neither", so there is a direction to move in.
    """
    _, half = routing_scores([row("k", {"billing"})], [item("k", "billing", "reporting")])
    _, none = routing_scores([row("k", {"shipping"})], [item("k", "billing", "reporting")])

    assert 0.0 < half < 1.0
    assert none == 0.0
    assert half > none


def test_an_extra_skill_costs_soft_without_zeroing_it():
    _, soft = routing_scores([row("k", {"billing", "shipping"})], [item("k", "billing")])
    assert 0.0 < soft < 1.0


# --- The third answer --------------------------------------------------------


def test_a_question_with_no_trace_is_left_out_of_the_fraction():
    """Unknown is not zero — the rule the whole detector is built on.

    A Langfuse outage that scored as "the agent routed everything wrong" would
    reject every candidate and end the run having learned nothing, with the
    chart showing a collapse that never happened.
    """
    rows = [row("a", {"billing"}), row("b", None)]
    items = [item("a", "billing"), item("b", "billing")]

    hard, _ = routing_scores(rows, items)
    assert hard == 1.0, "scored on the one question that could be seen"


def test_a_question_with_no_tags_is_left_out_of_the_fraction():
    """An untagged question has no right answer to be scored against.

    The wizard excludes them from the groups, but a run started before that, or
    against a set edited since, can still hold one — and guessing that "read
    nothing" was correct for it would be inventing a measurement.
    """
    rows = [row("a", {"billing"}), row("b", set())]
    items = [item("a", "billing"), item("b")]

    hard, _ = routing_scores(rows, items)
    assert hard == 1.0


def test_nothing_measurable_is_none_rather_than_zero():
    hard, soft = routing_scores([row("a", None)], [item("a", "billing")])
    assert hard is None and soft is None


def test_a_failed_question_is_not_a_routing_miss():
    """An agent timeout is not the description being wrong."""
    failed = ResultRow(item_key="a", correlation_id="c", status="failed",
                       failure_kind="agent")
    hard, _ = routing_scores([failed, row("b", {"billing"})],
                             [item("a", "billing"), item("b", "billing")])
    assert hard == 1.0


# --- Through the rollout summary --------------------------------------------


def test_the_summary_carries_routing_and_judge_scores_side_by_side():
    """Both, always. The one that gates is the run's choice; the other is how a
    developer finds out that routing improved and the answers did not."""
    rows = [row("a", {"billing"}, verdict="incorrect"), row("b", {"billing"})]
    items = [item("a", "billing"), item("b", "billing")]

    summary = score_rollout(rows, split="val", skill_step_no=1, items=items)

    assert summary.routing_hard == 1.0, "both questions routed correctly"
    assert summary.hard == 0.5, "and one of them was answered wrongly"


def test_routing_scores_are_absent_when_no_items_were_supplied():
    """Isolated passes none; nothing should invent a routing number for it."""
    summary = score_rollout([row("a", {"billing"})], split="val", skill_step_no=1)

    assert summary.routing_hard is None
    assert summary.routing_soft is None
    assert summary.hard == 1.0


def test_a_refused_rollout_reports_no_routing_score_either():
    """Past the error threshold nothing is scored — that has to include this.

    A routing number computed from whichever questions an outage happened to
    spare is exactly the kind of measurement the refusal exists to prevent.
    """
    rows = [
        ResultRow(item_key=str(i), correlation_id="c", status="failed",
                  failure_kind="agent")
        for i in range(4)
    ] + [row("ok", {"billing"})]
    items = [item(str(i), "billing") for i in range(4)] + [item("ok", "billing")]

    summary = score_rollout(
        rows, split="val", skill_step_no=1, items=items, error_threshold=0.2,
    )

    assert summary.aborted is True
    assert summary.routing_hard is None
    assert summary.hard is None


# --- What the analyst is told a "failure" is --------------------------------
#
# `update._reflect` splits a minibatch on `item["hard"]`: zeroes go to the
# failure analyst, ones to the success analyst. Which number that is has to be
# the number the run is optimising, or the two disagree — and the disagreement
# is silent and points the wrong way.


def test_routing_reflects_on_the_questions_that_routed_wrongly():
    """A question answered correctly *despite* opening the wrong skill is a
    routing failure, and the failure analyst is the one that must see it.

    Sent to the success analyst it does active harm: that prompt opens with
    "These questions were answered correctly, so the routing worked", so a
    routing failure would be presented to the model as proof the description is
    right and should not be touched.
    """
    from app.optimizer.reflection import analyst_item
    from app.optimizer.trajectory import Trajectory

    correct_answer_wrong_skill = analyst_item(
        row("k", {"reporting"}, verdict="correct"),
        trajectory=Trajectory(), question="q", ground_truth="a",
        mode="routing", gt_skills=("billing",),
    )

    assert correct_answer_wrong_skill["hard"] == 0.0


def test_routing_treats_a_wrong_answer_that_routed_correctly_as_a_success():
    """The mirror image. The description did its job; the body did not.

    That is not something a description edit can fix, and showing it to the
    failure analyst invites one — usually by narrowing the description so the
    question stops arriving, which is the exact degenerate move the gate is
    built to refuse.
    """
    from app.optimizer.reflection import analyst_item
    from app.optimizer.trajectory import Trajectory

    item_ = analyst_item(
        row("k", {"billing"}, verdict="incorrect"),
        trajectory=Trajectory(), question="q", ground_truth="a",
        mode="routing", gt_skills=("billing",),
    )

    assert item_["hard"] == 1.0


def test_isolated_still_splits_on_the_judge():
    """Unchanged, and it must be: an isolated run is optimising the answers."""
    from app.optimizer.reflection import analyst_item
    from app.optimizer.trajectory import Trajectory

    item_ = analyst_item(
        row("k", {"reporting"}, verdict="correct"),
        trajectory=Trajectory(), question="q", ground_truth="a",
        mode="isolated", gt_skills=("billing",),
    )

    assert item_["hard"] == 1.0


def test_the_analyst_is_told_which_skills_were_read_and_which_were_wanted():
    """Structured, rather than left for the model to infer from the trajectory.

    The evidence lives in tool results, and `trajectory.py` cuts those first
    when a minibatch is over budget — so on exactly the long trajectories a
    routing failure is most likely to hide in, the proof of it is the first
    thing to go.
    """
    from app.optimizer.reflection import analyst_item
    from app.optimizer.trajectory import Trajectory

    item_ = analyst_item(
        row("k", {"reporting", "shipping"}, verdict="correct"),
        trajectory=Trajectory(), question="q", ground_truth="a",
        mode="routing", gt_skills=("billing",),
    )

    assert item_["gt_skills"] == ["billing"]
    assert item_["skills_read"] == ["reporting", "shipping"]
    assert "billing" in item_["fail_reason"]


def test_the_routing_facts_reach_the_prompt_the_analyst_reads():
    """A field on the item dict that nothing renders is not evidence.

    The facts are put in structured fields rather than left to be read out of a
    conversation, and that only helps if what is rendered prints them. Routing
    renders a digest now rather than a trajectory, so this asks the digest.
    """
    from app.optimizer.reflection import analyst_item
    from app.optimizer.routing_digest import render_digest
    from app.optimizer.trajectory import Trajectory

    text = render_digest(
        [
            analyst_item(
                row("k", {"reporting"}, verdict="correct"),
                trajectory=Trajectory(), question="the question", ground_truth="a",
                mode="routing", gt_skills=("billing",),
            )
        ],
        targets=["billing", "reporting"],
    )

    # Which skill wanted it, which one it actually opened, and the question that
    # says why either might have been reasonable.
    assert "billing" in text and "reporting" in text
    assert "the question" in text


def test_a_routing_failure_is_not_attributed_to_the_judge():
    """A question graded correct that opened the wrong skill is a routing miss.

    The judge has nothing to do with it, and a page or a prompt that says
    otherwise sends the reader — or the analyst — to look at the answer instead
    of the description. The digest never mentions the judge at all: it is built
    from the tags and what was opened, and those are the whole of the verdict.
    """
    from app.optimizer.reflection import analyst_item
    from app.optimizer.routing_digest import render_digest
    from app.optimizer.trajectory import Trajectory

    item_ = analyst_item(
        row("k", {"reporting"}, verdict="correct"),
        trajectory=Trajectory(), question="q", ground_truth="a",
        mode="routing", gt_skills=("billing",),
    )
    text = render_digest([item_], targets=["billing", "reporting"])

    assert "judge" not in text.lower()
    # Correctly answered, wrongly routed: a miss for billing, not a success.
    assert item_["hard"] == 0.0
    assert "opened reporting instead" in text


def test_an_unobservable_question_never_counts_against_the_candidate():
    """End to end for the tri-state, through the detector's own output.

    Built by calling the detector rather than by hand-writing `None`, because
    the bug this pins was the detector and the scorer disagreeing about which
    value means "nothing was seen".
    """
    from app.optimizer.detector import detect_activation

    unobservable = detect_activation(
        None, skill_name="billing", skill_files={}, workspace_files={},
    )
    row = ResultRow(
        item_key="k", correlation_id="c", status="done", verdict="correct",
        skills_read=unobservable.skills_read,
    )

    assert routing_scores([row], [item("k", "billing")]) == (None, None)
