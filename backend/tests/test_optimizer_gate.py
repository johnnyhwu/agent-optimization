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
upstream). What is ours is the wrapper: which metric goes in, how a refusal is
labelled, and the second guard that only routing mode has.
"""
from __future__ import annotations

import pytest

from app.optimizer.gating import decide_gate


def gate(**overrides):
    """A gate call with sensible middles, so each test states only its point."""
    kwargs = dict(
        mode="isolated",
        step_no=3,
        cand_hard=0.5,
        cand_soft=0.5,
        cand_activation=None,
        current_score=0.5,
        current_activation=None,
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


# --- Routing mode's second guard -------------------------------------------


def test_routing_refuses_a_candidate_whose_activation_dropped():
    """Routing mode can improve accuracy by getting the skill *ignored*.

    This is the failure the guard exists for, and it is not hypothetical: a
    description narrowed until the agent stops opening the skill will raise
    accuracy on every question the skill was answering badly. Accuracy alone
    would call that an improvement and the run would optimise the skill out of
    existence, one accepted step at a time.
    """
    outcome = gate(
        mode="routing", cand_hard=0.9, current_score=0.5,
        cand_activation=0.4, current_activation=0.8,
    )
    assert outcome.action == "reject"
    assert outcome.reject_reason == "activation"


def test_routing_accepts_when_activation_holds_steady():
    """The rule is 'must not decrease', not 'must increase'.

    A description edit that fixes wording without changing which questions route
    to the skill is a legitimate improvement; requiring activation to rise as
    well would reject most of the good ones.
    """
    outcome = gate(
        mode="routing", cand_hard=0.7, current_score=0.5,
        cand_activation=0.8, current_activation=0.8,
    )
    assert outcome.accepted is True
    assert outcome.reject_reason is None


def test_routing_does_not_reject_on_an_unknown_activation_rate():
    """Unknown is not zero, and treating it as a drop would block every step.

    Activation is unobservable when no trace landed. If a missing rate counted
    as 0.0, a Langfuse outage would silently turn into 'every candidate rejected'
    and the run would end having learned nothing, with no indication why.
    """
    unknown_candidate = gate(
        mode="routing", cand_hard=0.9, current_score=0.5,
        cand_activation=None, current_activation=0.8,
    )
    unknown_current = gate(
        mode="routing", cand_hard=0.9, current_score=0.5,
        cand_activation=0.4, current_activation=None,
    )
    assert unknown_candidate.accepted is True
    assert unknown_current.accepted is True


def test_isolated_mode_ignores_activation_entirely():
    """The guard belongs to routing alone.

    An isolated run sends only this skill, so there is no competitor to lose to
    and a dip in activation is just the agent answering from its own knowledge.
    Applying the guard there would reject candidates for a reason the mode
    cannot act on.
    """
    outcome = gate(
        mode="isolated", cand_hard=0.9, current_score=0.5,
        cand_activation=0.1, current_activation=0.9,
    )
    assert outcome.action == "accept_new_best"
    assert outcome.reject_reason is None


def test_routing_rejects_on_accuracy_before_it_looks_at_activation():
    """A candidate that failed both guards is reported against the first.

    Otherwise a step that was simply worse would be labelled an activation
    problem, sending the developer to rewrite a description that was not the
    cause.
    """
    outcome = gate(
        mode="routing", cand_hard=0.2, current_score=0.5,
        cand_activation=0.1, current_activation=0.9,
    )
    assert outcome.reject_reason == "accuracy"


def test_an_activation_refusal_keeps_the_previous_scores():
    """The second guard must roll back as completely as the first.

    A candidate rejected for activation had a *higher* accuracy, so leaking its
    score into `current_score` would raise the bar for every later step — the
    run would then be comparing against a skill it decided not to keep.
    """
    outcome = gate(
        mode="routing", cand_hard=0.9, current_score=0.5,
        best_score=0.6, best_step=2,
        cand_activation=0.1, current_activation=0.9,
    )
    assert outcome.current_score == pytest.approx(0.5)
    assert outcome.best_score == pytest.approx(0.6)
    assert outcome.best_step == 2
