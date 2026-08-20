"""When a run stops before it has run out of steps, and why.

Four conditions, one mechanism. Two of them are the agent server falling over,
two are the run having stopped being worth paying for:

    train errors    too much of a *training* batch never came back, too many
                    steps in a row. There is nothing to reflect on, so those
                    steps produced no candidate at all.
    val errors      the same on the *validation* split, which is worse: the
                    gate's whole job is to compare two numbers, and a split
                    measured on the questions that happened to answer is not a
                    smaller measurement but an unrepresentative one.
    patience        no step has beaten the best score for this many steps.
    target          validation reached the number that was being aimed for.

Before this module there was no early stopping at all — a run executed
`num_epochs × steps_per_epoch` steps and stopped — *except* for one rule buried
in the rollout helper: a split that failed twice in a row killed the whole run.
That rule was invisible in the wizard, unconfigurable from the UI, and the most
destructive outcome available: an hour of paid agent calls thrown away over an
outage in the last five minutes of it. It is now this module's `val_errors` /
`train_errors` pair, which costs a step rather than a run.

Split from the engine for the same reason `gating.py` is: these are rules, the
engine is control flow, and rules that live inside an hour-long loop are rules
nobody can test. Everything here is a pure function over numbers.

**A share and a streak are a pair.** The share decides what counts as a bad
rollout — what fraction of one split may fail before its numbers are refused.
The streak decides how many bad rollouts in a row are a broken agent rather than
a bad afternoon. Neither means anything without the other, which is why they are
named and read together here and presented together in the wizard.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from app.config import settings

# The values `optimization_runs.stop_reason` can hold, and the strings the UI
# reads. `finished` is the ordinary end; the rest all mean the loop broke early.
# `cancelled` and `failed` are written by the engine, which is where those two
# outcomes are known.
STOP_FINISHED = "finished"
STOP_TRAIN_ERRORS = "early_stop_train_errors"
STOP_VAL_ERRORS = "early_stop_val_errors"
STOP_PATIENCE = "early_stop_patience"
STOP_TARGET = "early_stop_target"


@dataclass(frozen=True)
class StopPolicy:
    """The six numbers a run was started with.

    Resolved once per run rather than read per step, because a policy assembled
    from `config.get(...)` at each use site is a policy that can disagree with
    itself — and because the fallbacks below are precisely where a `0` gets
    silently turned back into a default.
    """

    train_error_share: float
    train_error_streak: int
    val_error_share: float
    val_error_streak: int
    patience: int
    target_score: float | None

    @classmethod
    def from_config(cls, config: Mapping | None) -> "StopPolicy":
        """Read a run's policy out of its stored config.

        Three sources in order: what this run was started with, the single
        `error_threshold` that used to govern both splits, then the
        environment's defaults. The middle one is only for runs created before
        the split thresholds existed — an interrupted run of that vintage
        resumes with the tolerance it was started with rather than a new one.
        """
        config = config or {}
        legacy = _number(config, "error_threshold", None)
        return cls(
            train_error_share=_number(
                config, "early_stop_train_error_share",
                legacy if legacy is not None else settings.early_stop_train_error_share,
            ),
            train_error_streak=int(_number(
                config, "early_stop_train_error_streak",
                settings.early_stop_train_error_streak,
            )),
            val_error_share=_number(
                config, "early_stop_val_error_share",
                legacy if legacy is not None else settings.early_stop_val_error_share,
            ),
            val_error_streak=int(_number(
                config, "early_stop_val_error_streak",
                settings.early_stop_val_error_streak,
            )),
            patience=int(_number(
                config, "early_stop_patience", settings.early_stop_patience
            )),
            target_score=_number(
                config, "early_stop_target_score", settings.early_stop_target_score
            ),
        )

    def error_share(self, split: str) -> float:
        """The share of one split that may fail before its numbers are refused."""
        return self.train_error_share if split == "train" else self.val_error_share

    def as_dict(self) -> dict:
        """The policy as the run's config stores it, so the page can show it."""
        return {
            "early_stop_train_error_share": self.train_error_share,
            "early_stop_train_error_streak": self.train_error_streak,
            "early_stop_val_error_share": self.val_error_share,
            "early_stop_val_error_streak": self.val_error_streak,
            "early_stop_patience": self.patience,
            "early_stop_target_score": self.target_score,
        }


@dataclass
class StopCounters:
    """How many rollouts in a row have been refused, per split.

    Consecutive, not cumulative: three bad rollouts spread over a forty-step run
    is a flaky afternoon, three in a row is an agent server that has stopped
    answering. One good rollout is enough to say the outage is over.

    Rebuilt from the step rows when a run resumes (`store.load_resume_state`),
    because a counter that resets on every restart is a counter a crash-looping
    backend can never reach.
    """

    train_errors: int = 0
    val_errors: int = 0

    def record(self, split: str, *, refused: bool) -> None:
        streak = (self.train_errors if split == "train" else self.val_errors) + 1
        value = streak if refused else 0
        if split == "train":
            self.train_errors = value
        else:
            self.val_errors = value


def decide_stop(
    policy: StopPolicy,
    counters: StopCounters,
    *,
    step_no: int,
    best_step: int,
    last_val_score: float | None,
) -> str | None:
    """The reason this run should stop now, or None to carry on.

    Checked after a step has been recorded, so `step_no` is a step that
    finished. Order is deliberate: an outage explains everything else that
    looks wrong, and reaching the target is a better thing to say about a run
    than running out of patience on the same step.

    `last_val_score` is None for a step whose validation was refused or never
    run — which is exactly the number the target must not be tested against.
    """
    if policy.train_error_streak > 0 and counters.train_errors >= policy.train_error_streak:
        return STOP_TRAIN_ERRORS
    if policy.val_error_streak > 0 and counters.val_errors >= policy.val_error_streak:
        return STOP_VAL_ERRORS
    # `> 0` for the same reason the other three read `> 0`: every number on this
    # form means "off" at zero, and a target of 0% is reached by every run on its
    # first step — which would make a mistyped digit look like the feature
    # working.
    if (
        policy.target_score is not None
        and policy.target_score > 0
        and last_val_score is not None
        and last_val_score >= policy.target_score
    ):
        return STOP_TARGET
    # `> 0` because 0 is off, and `>=` because a patience of 3 means three steps
    # have now failed to beat the best — the third one included.
    if policy.patience > 0 and step_no - best_step >= policy.patience:
        return STOP_PATIENCE
    return None


def _number(config: Mapping, key: str, fallback):
    """`config[key]` when it was actually set, else *fallback*.

    Not `config.get(key) or fallback`, which is the idiom the rest of this
    package uses and the one thing that cannot be used here: every number in
    this module has a meaningful zero. `patience: 0` means "never stop early"
    and `error_share: 0` means "tolerate nothing", and both would come back out
    as the default.
    """
    value = config.get(key)
    if value is None:
        return fallback
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return fallback
        return float(text)
    return value
