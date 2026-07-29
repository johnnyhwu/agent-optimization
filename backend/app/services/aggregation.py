"""Aggregations for the three-tier UI (§6.13).

Pure functions over plain dicts so they're trivially testable; routers build the
inputs from DB rows.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass


def result_phase(status: str, agent_response: str | None, verdict: str | None) -> str:
    """How far one question got, as the left column paints it.

        pending    no agent answer yet                      (grey)
        answered   answered, judge hasn't ruled yet         (plain)
        judged     has a verdict                            (green / red)
        failed     agent or judge errored                   (error styling)
        cancelled  the run was stopped mid-question

    Derived rather than stored: `status`, `agent_response` and `verdict` already
    say all of this, and a stored copy is one more thing that can drift. Kept
    here so the REST payload and the live SSE events can't disagree about a
    question's colour.
    """
    if status in ("failed", "cancelled"):
        return status
    if verdict is not None:
        return "judged"
    if agent_response is not None:
        return "answered"
    return "pending"


@dataclass
class RunVerdicts:
    """A run's verdicts, newest-relevant ordering handled by caller."""
    run_id: uuid.UUID
    started_at: object  # datetime; only used for ordering by caller
    verdicts: dict[uuid.UUID, str]  # question_pk -> 'correct' | 'incorrect'


def regression_summary(runs_newest_first: list[RunVerdicts]) -> dict:
    """Between the latest two runs: how many questions went correct -> incorrect
    (regressed) and incorrect -> correct (improved). Card shows the numbers only.
    """
    if len(runs_newest_first) < 2:
        return {"regressed": 0, "improved": 0}
    latest, prev = runs_newest_first[0], runs_newest_first[1]
    regressed = improved = 0
    for qpk, v in latest.verdicts.items():
        pv = prev.verdicts.get(qpk)
        if pv == "correct" and v == "incorrect":
            regressed += 1
        elif pv == "incorrect" and v == "correct":
            improved += 1
    return {"regressed": regressed, "improved": improved}


def incorrect_by_mode(
    runs_newest_first: list[RunVerdicts], mode: str, last_n: int = 2
) -> set[uuid.UUID]:
    """Which question_pks count as incorrect across selected runs (§6.13).

    - union         : wrong in ANY selected run
    - intersection  : wrong in ALL selected runs it appears in ("stubbornly wrong")
    - last_n        : wrong in ALL of the most recent N selected runs ("recent/regression")
    """
    if not runs_newest_first:
        return set()

    if mode == "union":
        out: set[uuid.UUID] = set()
        for r in runs_newest_first:
            out |= {q for q, v in r.verdicts.items() if v == "incorrect"}
        return out

    if mode == "intersection":
        considered = runs_newest_first
    elif mode == "last_n":
        considered = runs_newest_first[: max(1, last_n)]
    else:
        raise ValueError(f"unknown incorrect mode: {mode}")

    # question_pk must be wrong in every considered run it appears in, and appear
    # in all of them.
    all_qpks: set[uuid.UUID] = set()
    for r in considered:
        all_qpks |= set(r.verdicts.keys())
    out = set()
    for q in all_qpks:
        appearances = [r.verdicts.get(q) for r in considered]
        if all(v == "incorrect" for v in appearances):  # None (absent) fails the all()
            out.add(q)
    return out
