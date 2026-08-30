"""The validation gate: one comparison, on whichever score the mode is about.

The decision itself is upstream's (`vendor/gate.py`, byte-identical): a
candidate is kept only if it beats the skill currently in force, and it becomes
the new best only if it beats the best seen so far. Strictly — a tie is a
rejection, because validation accuracy on a few dozen questions moves a question
at a time and `>=` would let the skill take a random walk through edits the data
never supported.

**Both modes take the same path.** What differs is upstream of here: an isolated
run hands over the judge's accuracy, a routing run hands over routing accuracy
(`engine._score_of`). The gate compares the numbers it is given.

There used to be a second guard, for routing only: the target skill's activation
rate must not fall. It existed because routing has a way to improve *judge*
accuracy that nobody wants — narrow the description until the agent stops
opening the skill, and every question it was answering badly gets answered from
the model's own knowledge instead — and an accuracy-only gate would accept that
one approved step at a time while the chart climbed.

It watched one skill, in one direction, and the mirror image walked straight
past it: a description widened to claim every question keeps its own activation
at 100% and starves every other skill on the agent. Gating on routing accuracy
closes both, because both are the same error measured properly — the questions
tagged for a skill stopped reaching it, or questions belonging to another skill
started reaching this one. A proxy needed a guard; the real thing does not.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.optimizer.vendor.gate import evaluate_gate, select_gate_score


@dataclass(frozen=True)
class GateOutcome:
    """What the gate decided, and the state the next step inherits."""

    action: str  # accept_new_best | accept | reject
    reject_reason: str | None  # accuracy | None
    candidate_score: float
    current_score: float
    best_score: float
    best_step: int

    @property
    def accepted(self) -> bool:
        return self.action != "reject"


def decide_gate(
    *,
    step_no: int,
    cand_hard: float,
    cand_soft: float = 0.0,
    current_score: float,
    best_score: float,
    best_step: int,
    metric: str = "hard",
    mixed_weight: float = 0.5,
) -> GateOutcome:
    """Accept or reject one candidate, and say which guard refused it.

    `candidate_score` is returned separately from `current_score` because they
    diverge precisely when the answer is "reject" — and the chart plots the
    candidate's score at that step regardless of whether it was kept. Reading
    the plotted value off `current_score` would draw a flat line through every
    rejection, hiding exactly the steps a developer most wants to look at.
    """
    candidate_score = select_gate_score(cand_hard, cand_soft, metric, mixed_weight)

    result = evaluate_gate(
        candidate_skill="",
        cand_hard=cand_hard,
        cand_soft=cand_soft,
        current_skill="",
        current_score=current_score,
        best_skill="",
        best_score=best_score,
        best_step=best_step,
        global_step=step_no,
        metric=metric,
        mixed_weight=mixed_weight,
    )

    if result.action == "reject":
        return GateOutcome(
            action="reject", reject_reason="accuracy", candidate_score=candidate_score,
            current_score=result.current_score, best_score=result.best_score,
            best_step=result.best_step,
        )

    return GateOutcome(
        action=result.action, reject_reason=None, candidate_score=candidate_score,
        current_score=result.current_score, best_score=result.best_score,
        best_step=result.best_step,
    )
