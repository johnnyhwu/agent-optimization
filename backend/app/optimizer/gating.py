"""The validation gate, and the second guard that only routing mode has.

The decision itself is upstream's (`vendor/gate.py`, byte-identical): a
candidate is kept only if it beats the skill currently in force, and it becomes
the new best only if it beats the best seen so far. Strictly — a tie is a
rejection, because validation accuracy on a few dozen questions moves a question
at a time and `>=` would let the skill take a random walk through edits the data
never supported.

What is added here is routing mode's activation guard, and it exists because
routing mode has a way to improve accuracy that nobody wants:

    narrow the description until the agent stops opening the skill at all.

Every question the skill was answering badly then gets answered by the agent's
own knowledge instead, and accuracy goes up. An accuracy-only gate calls that an
improvement and accepts it, and the run optimises the skill out of existence one
approved step at a time — while the chart climbs. So a routing candidate must
also not *lose* ground: activation may hold or rise, never fall.

Isolated mode gets no such guard. It sends one skill and no alternatives, so
there is no competitor to lose to and a dip in activation is just the agent
answering from memory — a fact about the question, not about the edit.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.optimizer.vendor.gate import evaluate_gate, select_gate_score


@dataclass(frozen=True)
class GateOutcome:
    """What the gate decided, and the state the next step inherits."""

    action: str  # accept_new_best | accept | reject
    reject_reason: str | None  # accuracy | activation | None
    candidate_score: float
    current_score: float
    best_score: float
    best_step: int

    @property
    def accepted(self) -> bool:
        return self.action != "reject"


def decide_gate(
    *,
    mode: str,
    step_no: int,
    cand_hard: float,
    cand_soft: float = 0.0,
    cand_activation: float | None = None,
    current_score: float,
    current_activation: float | None = None,
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

    # Accuracy is satisfied. Routing has one more thing to check — and only when
    # both rates are actually known: activation is unobservable when no trace
    # landed, and treating unknown as zero would turn a Langfuse outage into
    # "every candidate rejected", ending the run having learned nothing with no
    # indication why.
    if (
        mode == "routing"
        and cand_activation is not None
        and current_activation is not None
        and cand_activation < current_activation
    ):
        return GateOutcome(
            action="reject", reject_reason="activation", candidate_score=candidate_score,
            current_score=current_score, best_score=best_score, best_step=best_step,
        )

    return GateOutcome(
        action=result.action, reject_reason=None, candidate_score=candidate_score,
        current_score=result.current_score, best_score=result.best_score,
        best_step=result.best_step,
    )
