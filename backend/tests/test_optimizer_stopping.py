"""Why a run stops early — the four conditions, and the zeros that turn them off.

An optimization run is an hour of paid agent calls. Both halves of this module
are expensive to get wrong in opposite directions: a condition that fires when
it should not throws away a run that was working, and one that never fires
leaves a crash-looping agent server billing all afternoon for steps that measure
nothing.

The zeros are the part worth guarding hardest. `patience: 0` means "never stop
early" and `error_share: 0` means "tolerate nothing"; the idiom the rest of this
package uses to read config — `config.get(key) or default` — silently turns both
into whatever the environment's default happens to be. That is not a
hypothetical: it is why `_number` exists.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config import settings
from app.optimizer.stopping import (
    STOP_PATIENCE,
    STOP_TARGET,
    STOP_TRAIN_ERRORS,
    STOP_VAL_ERRORS,
    StopCounters,
    StopPolicy,
    decide_stop,
)
from app.optimizer.store import _trailing_streak


def policy(**overrides) -> StopPolicy:
    """A policy with everything off, so each test switches on only its subject."""
    kwargs = dict(
        train_error_share=0.25,
        train_error_streak=0,
        val_error_share=0.25,
        val_error_streak=0,
        patience=0,
        target_score=None,
    )
    kwargs.update(overrides)
    return StopPolicy(**kwargs)


def decide(pol, counters=None, *, step_no=5, best_step=5, last_val_score=None):
    return decide_stop(
        pol, counters or StopCounters(),
        step_no=step_no, best_step=best_step, last_val_score=last_val_score,
    )


# --- The counters -----------------------------------------------------------


def test_a_streak_is_consecutive_not_cumulative():
    """Three bad rollouts across a long run is a flaky afternoon.

    Three in a row is an agent server that has stopped answering, and only the
    second one is worth ending a run over.
    """
    counters = StopCounters()

    counters.record("val", refused=True)
    counters.record("val", refused=True)
    counters.record("val", refused=False)
    counters.record("val", refused=True)

    assert counters.val_errors == 1


def test_the_two_splits_count_separately():
    counters = StopCounters()

    counters.record("train", refused=True)
    counters.record("val", refused=False)

    assert counters.train_errors == 1
    assert counters.val_errors == 0


# --- The four conditions ----------------------------------------------------


def test_a_train_streak_stops_the_run():
    counters = StopCounters(train_errors=3)

    assert decide(policy(train_error_streak=3), counters) == STOP_TRAIN_ERRORS


def test_one_short_of_the_train_streak_carries_on():
    """The boundary, because a run ended one step early is a run ended wrongly."""
    counters = StopCounters(train_errors=2)

    assert decide(policy(train_error_streak=3), counters) is None


def test_a_val_streak_stops_the_run():
    counters = StopCounters(val_errors=2)

    assert decide(policy(val_error_streak=2), counters) == STOP_VAL_ERRORS


def test_patience_counts_steps_since_the_best_one():
    assert decide(policy(patience=3), step_no=7, best_step=4) == STOP_PATIENCE
    assert decide(policy(patience=3), step_no=6, best_step=4) is None


def test_a_new_best_this_step_resets_patience():
    assert decide(policy(patience=1), step_no=9, best_step=9) is None


def test_the_target_stops_the_run_when_validation_reaches_it():
    assert decide(policy(target_score=0.9), last_val_score=0.9) == STOP_TARGET
    assert decide(policy(target_score=0.9), last_val_score=0.89) is None


def test_a_step_with_no_validation_score_cannot_reach_the_target():
    """A refused validation split has no number, and None is not a low score.

    Testing the target against a step whose validation was thrown away would
    let an outage end the run by declaring success.
    """
    assert decide(policy(target_score=0.0), last_val_score=None) is None


def test_an_outage_is_reported_before_patience_runs_out():
    """Both are true; only one of them explains the other.

    A run whose last three validation splits never came back has not "stopped
    improving" — it has stopped measuring, and saying so is the difference
    between fixing the agent server and abandoning the experiment.
    """
    counters = StopCounters(val_errors=3)

    reason = decide(
        policy(val_error_streak=3, patience=1), counters, step_no=9, best_step=0
    )

    assert reason == STOP_VAL_ERRORS


# --- Off means off ----------------------------------------------------------


def test_zero_streaks_and_zero_patience_never_stop_a_run():
    counters = StopCounters(train_errors=99, val_errors=99)

    assert decide(policy(), counters, step_no=99, best_step=0) is None


def test_no_target_never_stops_a_run():
    assert decide(policy(), last_val_score=1.0) is None


def test_a_target_of_zero_is_off_rather_than_reached_immediately():
    """Every number on this form means "off" at zero, and this one has to agree.

    A target of 0% is met by every run on its first step, so reading it
    literally would make a mistyped digit look like the feature working.
    """
    assert decide(policy(target_score=0), last_val_score=0.0) is None


# --- Reading a run's policy back --------------------------------------------


def test_a_runs_own_numbers_win():
    resolved = StopPolicy.from_config({
        "early_stop_train_error_share": 0.5,
        "early_stop_train_error_streak": 2,
        "early_stop_val_error_share": 0.1,
        "early_stop_val_error_streak": 4,
        "early_stop_patience": 6,
        "early_stop_target_score": 0.95,
    })

    assert resolved.train_error_share == 0.5
    assert resolved.train_error_streak == 2
    assert resolved.val_error_share == 0.1
    assert resolved.val_error_streak == 4
    assert resolved.patience == 6
    assert resolved.target_score == 0.95


def test_a_stored_zero_survives_the_read():
    """The whole reason `_number` exists rather than `config.get(k) or default`.

    A developer who typed 0 asked for "never stop early"; handing them the
    environment's 3 instead is a run that ends for a reason nobody chose.
    """
    resolved = StopPolicy.from_config({
        "early_stop_patience": 0,
        "early_stop_val_error_streak": 0,
        "early_stop_val_error_share": 0,
    })

    assert resolved.patience == 0
    assert resolved.val_error_streak == 0
    assert resolved.val_error_share == 0


def test_an_older_runs_single_threshold_still_governs_both_splits():
    """Runs created before the split thresholds existed carry `error_threshold`.

    One of those can be resumed after this deploy, and it must resume with the
    tolerance it was started with rather than silently acquiring a new one.
    """
    resolved = StopPolicy.from_config({"error_threshold": 0.4})

    assert resolved.train_error_share == 0.4
    assert resolved.val_error_share == 0.4


def test_an_empty_config_falls_back_to_the_environment():
    resolved = StopPolicy.from_config({})

    assert resolved.train_error_share == settings.early_stop_train_error_share
    assert resolved.val_error_streak == settings.early_stop_val_error_streak
    assert resolved.patience == settings.early_stop_patience
    assert resolved.target_score == settings.early_stop_target_score


def test_a_blank_string_is_an_unset_field_not_a_zero():
    """docker-compose passes unset optional vars through as "".

    The same rule `Settings._blank_is_unset` applies, one layer up: a blank in a
    stored config means "the deployment did not say", not "stop at 0%".
    """
    resolved = StopPolicy.from_config({"early_stop_target_score": ""})

    assert resolved.target_score == settings.early_stop_target_score


@pytest.mark.parametrize("split,expected", [("train", 0.3), ("val", 0.1)])
def test_each_split_is_asked_about_its_own_share(split, expected):
    resolved = policy(train_error_share=0.3, val_error_share=0.1)

    assert resolved.error_share(split) == expected


# --- Rebuilding the counters after a restart --------------------------------
#
# `store._trailing_streak` is the resume path's copy of `StopCounters`: the same
# rule, applied to rows on disk rather than to a loop's memory. If the two
# disagree, a run that restarts mid-outage gets a fresh allowance of steps every
# time the backend bounces — which is exactly the situation the streak exists
# for.


def steps(*reasons):
    """Step rows, oldest first, identified only by why they were refused."""
    return [
        SimpleNamespace(step_no=i, gate_reject_reason=reason)
        for i, reason in enumerate(reasons)
    ]


def test_the_streak_is_read_from_the_end_of_the_run():
    rows = steps(None, "val_errors", None, "val_errors", "val_errors")

    assert _trailing_streak(rows, "val_errors") == 2


def test_a_clean_step_ends_the_streak():
    assert _trailing_streak(steps("val_errors", None), "val_errors") == 0


def test_a_step_that_never_reached_validation_does_not_clear_the_val_streak():
    """A training batch that never came back says nothing about validation.

    Counting it as a clean validation would hand a broken agent server a fresh
    three steps every time the training half failed too — which, when the agent
    server is down, is every step.
    """
    rows = steps("val_errors", "train_errors", "val_errors")

    assert _trailing_streak(rows, "val_errors") == 2


def test_a_refused_validation_does_clear_the_train_streak():
    """Reaching validation at all means the training rollout came back."""
    rows = steps("train_errors", "val_errors")

    assert _trailing_streak(rows, "train_errors") == 0


def test_the_baseline_is_neutral_for_the_training_streak():
    """Step 0 answers validation only, so it never had a training rollout."""
    rows = [SimpleNamespace(step_no=0, gate_reject_reason=None)] + steps("train_errors")[:1]
    rows[1] = SimpleNamespace(step_no=1, gate_reject_reason="train_errors")

    assert _trailing_streak(rows, "train_errors") == 1
