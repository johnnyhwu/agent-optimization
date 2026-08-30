"""The validation gate — the one thing standing between a run and its own noise.

Every step of an optimization run produces a candidate skill that *some* model
argued for. The gate is what decides whether that argument survives contact with
held-out data, and it is the only place in the loop where a change can be
refused. A gate that is one comparison too lenient does not fail loudly: the run
completes, the chart goes up, and the skill that comes out has been fitted to
sampling noise.

So these tests are mostly about the boundaries — ties, unknowns, and the case
where one guard is happy and the other is not.

The decision itself is SkillOpt's (`vendor/gate.py`, byte-identical to
upstream). What is ours is the wrapper: which numbers go in, and how a refusal
is labelled.

There used to be a second guard here, for routing mode only: activation must not
fall. It is gone, along with the hole in it — see the routing-mode section
below.
"""
from __future__ import annotations

import pytest

from app.optimizer.gating import decide_gate


def gate(**overrides):
    """A gate call with sensible middles, so each test states only its point."""
    kwargs = dict(
        step_no=3,
        cand_hard=0.5,
        cand_soft=0.5,
        current_score=0.5,
        best_score=0.5,
        best_step=1,
    )
    kwargs.update(overrides)
    return decide_gate(**kwargs)


# --- The accuracy comparison ------------------------------------------------


def test_strictly_better_than_current_is_accepted():
    """A candidate that beats the current skill is kept.

    The base case: without it the loop could never make progress at all.
    """
    outcome = gate(cand_hard=0.6, current_score=0.5, best_score=0.9)
    assert outcome.action == "accept"
    assert outcome.accepted is True
    assert outcome.reject_reason is None


def test_a_tie_is_rejected():
    """Equal is not better, and accepting equal is how a run drifts.

    Validation accuracy on a few dozen questions moves by a question at a time.
    If `>=` were the test, every candidate that changed nothing measurable would
    still be adopted, and the skill would take a random walk through edits that
    the data never supported — while the chart showed a flat, healthy line.
    """
    outcome = gate(cand_hard=0.5, current_score=0.5)
    assert outcome.action == "reject"
    assert outcome.reject_reason == "accuracy"


def test_worse_than_current_is_rejected_with_the_accuracy_reason():
    """A refusal has to say which guard refused it.

    'Rejected' alone leaves the developer looking at a diff with no way to tell
    whether the edits were unhelpful or whether they broke routing — two
    different problems with two different fixes.
    """
    outcome = gate(cand_hard=0.3, current_score=0.5)
    assert outcome.action == "reject"
    assert outcome.reject_reason == "accuracy"


def test_better_than_current_but_not_the_best_is_accepted_without_becoming_best():
    """Accepting a step must not overwrite a better skill found earlier.

    The download button offers 'best by validation'. If any accepted step
    replaced the best, a run that climbed to 0.8 and then wandered down to 0.6
    would hand the developer the 0.6 skill.
    """
    outcome = gate(cand_hard=0.6, current_score=0.5, best_score=0.8, best_step=1)
    assert outcome.action == "accept"
    assert outcome.best_score == pytest.approx(0.8)
    assert outcome.best_step == 1


def test_beating_the_best_records_this_step_as_the_new_best():
    outcome = gate(cand_hard=0.9, current_score=0.5, best_score=0.8, best_step=1, step_no=4)
    assert outcome.action == "accept_new_best"
    assert outcome.best_score == pytest.approx(0.9)
    assert outcome.best_step == 4


def test_a_rejected_candidate_leaves_current_and_best_exactly_where_they_were():
    """Reject means roll back, and the scores are what the next step compares to.

    If a rejected candidate's score leaked into `current_score`, the next step
    would be measured against a skill nobody is running.
    """
    outcome = gate(cand_hard=0.1, current_score=0.55, best_score=0.77, best_step=2)
    assert outcome.current_score == pytest.approx(0.55)
    assert outcome.best_score == pytest.approx(0.77)
    assert outcome.best_step == 2


# --- Which metric is compared ----------------------------------------------


def test_the_soft_metric_can_be_the_one_that_decides():
    """A validation split of a dozen questions cannot move `hard` by one edit.

    Hard accuracy is a step function on a small split: a genuine improvement
    that turns a 0.4-scored answer into a 0.9-scored one moves nothing at all.
    Partial credit is what makes those steps visible, which is exactly why the
    judge's score is 0..1 in this feature.
    """
    outcome = gate(metric="soft", cand_hard=0.5, cand_soft=0.7, current_score=0.6)
    assert outcome.action in ("accept", "accept_new_best")
    assert outcome.current_score == pytest.approx(0.7)


def test_hard_is_the_default_metric_and_soft_does_not_leak_into_it():
    """With the default metric, a candidate that only improved `soft` is refused.

    Guards against the wrapper quietly passing a blended score when nobody asked
    for one — the gate would then be measuring something other than what the
    chart and the step table display.
    """
    outcome = gate(cand_hard=0.5, cand_soft=0.99, current_score=0.5)
    assert outcome.action == "reject"


def test_mixed_weights_the_two_metrics():
    outcome = gate(
        metric="mixed", mixed_weight=0.5,
        cand_hard=0.4, cand_soft=0.8, current_score=0.5,
    )
    assert outcome.current_score == pytest.approx(0.6)
    assert outcome.action in ("accept", "accept_new_best")


# --- Routing mode ----------------------------------------------------------
#
# Routing runs feed the gate routing accuracy instead of judge accuracy
# (`engine._score_of`), so everything above applies unchanged and there is no
# second comparison. What that replaced was a guard requiring the target
# skill's activation not to fall, and the tests below are the two behaviours
# that mattered, re-stated against the metric that now carries them.


def test_a_description_narrowed_until_the_skill_is_ignored_is_refused():
    """The failure the old activation guard existed for.

    A description narrowed until the agent stops opening the skill raises judge
    accuracy on every question the skill was answering badly — the answers come
    from the model's own knowledge instead — so a run gated on judge accuracy
    would optimise the skill out of existence, one accepted step at a time,
    with the chart climbing all the way.

    Gated on routing accuracy it is refused on the first step: the questions
    tagged for this skill stopped reaching it, which is what the number counts.
    """
    outcome = gate(cand_hard=0.2, current_score=0.8)

    assert outcome.action == "reject"
    assert outcome.reject_reason == "accuracy"


def test_a_description_widened_until_it_wins_everything_is_refused_too():
    """The half the old guard could not see, and the reason it was replaced.

    A description broadened to claim every question keeps its own activation at
    100% — rising, even — while every other skill on the agent is starved. An
    activation-only guard reads that as an improvement. Routing accuracy counts
    a skill opened for a question that was not its job as the error it is, so
    the candidate scores worse and is refused.
    """
    outcome = gate(cand_hard=0.3, current_score=0.7)

    assert outcome.action == "reject"


def test_routing_accepts_a_candidate_that_routes_better():
    outcome = gate(cand_hard=0.9, current_score=0.5)

    assert outcome.action == "accept_new_best"
    assert outcome.reject_reason is None


def test_the_gate_does_not_take_a_mode_at_all():
    """Structural, not asserted: there is no argument to branch on.

    The caller chooses which pair of numbers to hand over and the gate compares
    them. A mode-conditional branch in here is how the previous guard came to
    apply one rule, in one direction, to one skill — so the parameter is gone
    rather than merely unused.
    """
    import inspect

    from app.optimizer.gating import decide_gate

    assert "mode" not in inspect.signature(decide_gate).parameters
