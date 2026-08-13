"""Scoring a rollout when the agent is a real service that sometimes fails.

Upstream SkillOpt never faces this: its environments score a prediction against
a gold answer locally, so every item in a batch produces a number. Here every
item is an HTTP call that can time out, 500, or come back unjudgeable — and what
we do with those decides whether the gradient means anything.

An agent timeout is **not the skill being wrong**. Scoring it as a wrong answer
hands the optimizer a gradient pointing at a network problem, and the gate then
accepts or rejects a skill edit on the strength of how flaky the last two minutes
were. So failures are excluded from every figure — accuracy, latency and
activation alike — and counted separately so the exclusion is visible instead of
implied.

Excluding them creates the opposite hazard, which is why the abort rule exists:
a step scored on 60% of its batch is not a smaller measurement, it is a
different and unrepresentative one, and the gate has no way to know. Past a
threshold the step is abandoned rather than scored. `docs/spec.md` makes the same
call about partial data on the upload path: "a set built from half the rows looks
normal but is wrong".
"""
from __future__ import annotations

import pytest

from app.optimizer.adapter import score_rollout
from app.optimizer.store import ResultRow


def ok(key: str, *, hard: str = "correct", soft: float = 1.0,
       latency: int = 1000, activated: bool | None = True) -> ResultRow:
    return ResultRow(
        item_key=key, correlation_id=key, status="done",
        verdict=hard, judge_score=soft, agent_latency_ms=latency,
        activated=activated,
    )


def agent_error(key: str, *, latency: int | None = None) -> ResultRow:
    return ResultRow(
        item_key=key, correlation_id=key, status="failed",
        failure_kind="agent", error_message="timeout after 120s",
        agent_latency_ms=latency,
    )


def judge_error(key: str) -> ResultRow:
    return ResultRow(
        item_key=key, correlation_id=key, status="failed",
        failure_kind="judge_invalid", error_message="unparseable judge output",
        agent_latency_ms=900, agent_response="an answer",
    )


def score(rows, **kw):
    return score_rollout(rows, split="train", skill_step_no=0, **kw)


# --- Failures are excluded, not counted as wrong ---------------------------


def test_a_failed_item_is_excluded_from_accuracy_rather_than_scored_zero():
    """Two correct answers and one timeout is 100%, not 67%.

    Scoring the timeout as wrong would make the skill look worse for a reason
    the skill cannot fix, and the next step's analyst would go hunting for a rule
    to explain a network error.
    """
    summary = score([ok("a"), ok("b"), agent_error("c")])

    assert summary.hard == 1.0
    assert summary.n_items == 3
    assert summary.n_scored == 2
    assert summary.n_agent_error == 1


def test_agent_and_judge_failures_are_counted_apart():
    """They indict different things, and one of them is the developer's to fix.

    `judge_invalid` means the judge replied and we could not parse it, which
    usually indicts this run's judge prompt — the one thing on the list the
    developer can go and change. Lumping it in with timeouts hides it, exactly as
    it would in an eval run (`models.py`, `failure_kind`).
    """
    summary = score([ok("a"), agent_error("b"), judge_error("c")])

    assert summary.n_agent_error == 1
    assert summary.n_judge_error == 1
    assert summary.n_scored == 1


def test_soft_score_uses_the_same_denominator_as_hard():
    """Two figures over two different denominators cannot be compared, and the
    mixed gate metric combines them."""
    summary = score([
        ok("a", hard="correct", soft=1.0),
        ok("b", hard="incorrect", soft=0.4),
        agent_error("c"),
    ])

    assert summary.hard == 0.5
    assert summary.soft == pytest.approx(0.7)


def test_latency_statistics_exclude_failed_items():
    """A timeout measures our limit, not how long the agent takes to answer.

    Folding it in makes the median jump by the timeout setting whenever the
    network hiccups, which reads on the chart as the skill having got slower.
    """
    summary = score([
        ok("a", latency=100), ok("b", latency=200), ok("c", latency=300),
        agent_error("d", latency=120_000),
    ])

    assert (summary.latency_min_ms, summary.latency_p50_ms, summary.latency_max_ms) == (100, 200, 300)


def test_activation_rate_is_measured_over_scored_items_only():
    """A fixed denominator is what makes two steps comparable.

    If failures moved the denominator, activation would drift step to step for
    reasons that have nothing to do with the skill — and in routing mode the gate
    guards on exactly this number.
    """
    summary = score([
        ok("a", activated=True), ok("b", activated=True),
        ok("c", activated=False), agent_error("d"),
    ])

    assert summary.n_activated == 2
    assert summary.activation_rate == pytest.approx(2 / 3)


def test_items_whose_activation_is_unknown_are_left_out_of_the_rate():
    """Unknown is not false; averaging it in as zero would invent a number.

    The rate then describes the items we could actually observe, and the UI shows
    how many that was.
    """
    summary = score([
        ok("a", activated=True), ok("b", activated=None), ok("c", activated=None),
    ])

    assert summary.activation_rate == 1.0
    assert summary.n_activated == 1


def test_activation_rate_is_none_when_nothing_could_be_observed():
    """Better an empty figure than a confident 0%."""
    summary = score([ok("a", activated=None), ok("b", activated=None)])

    assert summary.activation_rate is None


@pytest.mark.parametrize("count", [1, 2, 3, 4])
def test_median_latency_over_even_and_odd_counts(count):
    """The p50 is shown to a person as "typical"; an off-by-one here is silent."""
    rows = [ok(str(i), latency=(i + 1) * 100) for i in range(count)]
    expected = {1: 100, 2: 150, 3: 200, 4: 250}[count]

    assert score(rows).latency_p50_ms == expected


# --- The abort rule ---------------------------------------------------------


def test_a_step_is_aborted_when_too_much_of_the_batch_failed():
    """Half a batch is not a smaller gradient, it is a different one.

    And the gate cannot tell: it sees a number, accepts or rejects a skill edit
    on it, and that decision then contaminates every later step. Refusing to
    score is the only honest option.
    """
    rows = [ok("a"), ok("b"), agent_error("c"), agent_error("d")]

    summary = score(rows, error_threshold=0.2)

    assert summary.aborted is True
    assert summary.abort_reason
    assert "50" in summary.abort_reason or "0.5" in summary.abort_reason


def test_the_threshold_is_inclusive_so_exactly_at_the_limit_still_scores():
    """A boundary that moves under you is a bug nobody reproduces.

    Exactly 20% failed with a 20% threshold is within tolerance, not past it.
    """
    rows = [ok("a"), ok("b"), ok("c"), ok("d"), agent_error("e")]

    summary = score(rows, error_threshold=0.2)

    assert summary.aborted is False
    assert summary.hard == 1.0


def test_a_rollout_where_everything_failed_aborts_without_dividing_by_zero():
    summary = score([agent_error("a"), agent_error("b")])

    assert summary.aborted is True
    assert summary.hard is None and summary.soft is None
    assert summary.n_scored == 0


def test_an_empty_rollout_is_not_a_crash():
    """Reachable when every item was cancelled mid-step."""
    summary = score([])

    assert summary.n_items == 0 and summary.hard is None
    assert summary.aborted is False


# --- What the summary carries forward --------------------------------------


def test_the_summary_keeps_every_row_including_the_failed_ones():
    """Part 1 lists failures with a badge rather than hiding them.

    A question that vanished from the list would leave the developer counting
    rows to work out that something went wrong.
    """
    rows = [ok("a"), agent_error("b")]

    summary = score(rows)

    assert [r.item_key for r in summary.results] == ["a", "b"]
    assert summary.split == "train"


def test_the_summary_records_which_skill_version_was_measured():
    """Train is measured with the skill as it *entered* the step, validation with
    the candidate the step produced — two different skills at the same step
    number. Part 1 says which, or "running skill v2" would be a guess."""
    summary = score_rollout([ok("a")], split="val", skill_step_no=3)

    assert summary.skill_step_no == 3
    assert summary.split == "val"
