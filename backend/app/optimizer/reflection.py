"""Turning a scored rollout into what the analyst actually reads.

SkillOpt's reflect stage wants trajectories: a list of what the agent did, per
task, formatted into one prompt per minibatch. Its own environments produce
those locally and write them to disk. Ours arrive as Langfuse traces, which
changes three things.

**They have to be folded before they are measured.** A Langfuse trace is a list
of observations, and each observation of an LLM agent is a *whole* request — the
tool catalogue, the system prompt carrying the skill, and the entire history so
far. Concatenated span by span, one trajectory repeats all of that once per
step. `app/optimizer/trajectory.py` folds the spans back into the single
conversation they were snapshots of, which is both what a developer sees on the
Evaluation page and the difference between a prompt that fits and one that does
not.

**They have to be bounded.** Even folded, a trace is as long as the agent
happened to be chatty and a minibatch is several of them. So the budget is
shared out here (`allocate_budget`), each trajectory is cut to its share
(`truncate_trajectory`), and if the batch *still* does not fit, whole items are
dropped — because a prompt that overflows the model's context window fails
entirely, which is worse than an analyst reasoning about six trajectories
instead of eight and being told so.

**What was cut has to be recoverable.** The Part 1 page shows the prompt with
its elisions marked, because "the model did not fix this" and "the model never
saw this" look identical in the output and are opposite problems. The ledger
returned here is what lets the page tell them apart.
"""
from __future__ import annotations

from typing import Mapping, Sequence

from app.integrations.base import Trace
from app.optimizer.store import ResultRow
from app.optimizer.trajectory import (
    Trajectory,
    build_trajectory,
    trajectory_chars,
    truncate_trajectory,
)
from app.services.truncation import DEFAULT_MIN_KEEP, allocate_budget

# Roughly 35-40k tokens of trajectory per step, shared across its whole training
# batch. Large enough that ordinary traces pass through untouched — the first
# stage of the cascade is "measure, and cut nothing if it fits" — and small
# enough to leave a 100k-token model room for the skill, the analyst's
# instructions and its own 16k-token answer.
#
# It was 60,000, chosen when the budget was unreachable anyway: every span
# repeated an uncuttable copy of the system prompt, so the cascade could not
# meet any budget and every prompt went out oversized. Folding the repetition
# away made the number load-bearing for the first time, and 60,000 would then
# have started throwing away evidence that now fits.
DEFAULT_REFLECT_BUDGET_CHARS = 150_000


def conversation_from_trace(trace: Trace | None) -> Trajectory:
    """A trace as one conversation. See `app/optimizer/trajectory.py`."""
    return build_trajectory(trace)


def analyst_item(row: ResultRow, *, trajectory: Trajectory, question: str,
                 ground_truth: str) -> dict:
    """One rollout result in the shape the minibatch formatter expects.

    `hard` is what splits the batch into the failure analyst's group and the
    success analyst's, so it comes from the judge's verdict rather than from the
    score: a partially-correct answer is a failure to learn from, not a success
    to reinforce.

    `fail_reason` is the judge's own comment — the same sentence the Evaluation
    page shows under "Judge" — and it is labelled as the judge's in the prompt,
    because an analyst told only "this failed" cannot tell a wrong answer from a
    right one that was graded strictly.

    The gold answer travels as `reference_text`, rendered under "Ground-truth
    Response". Showing it is deliberate and it is why the analyst prompts forbid
    copying it: a model cannot explain why an answer was wrong without knowing
    what right looked like, and the defence against memorisation is the held-out
    split plus the leak check on the diff, not withholding the evidence.
    """
    return {
        "id": row.item_key,
        "hard": 1.0 if row.verdict == "correct" else 0.0,
        "soft": float(row.judge_score or 0.0),
        "task_description": question,
        "reference_text": ground_truth,
        "fail_reason": (row.judge_comment or "") if row.verdict != "correct" else "",
        "n_turns": len(trajectory.turns),
        "agent_response": row.agent_response or "",
        "trajectory": trajectory,
    }


def build_analyst_items(
    rows: Sequence[ResultRow],
    *,
    questions: Mapping[str, str] | None = None,
    ground_truths: Mapping[str, str] | None = None,
    budget_chars: int = DEFAULT_REFLECT_BUDGET_CHARS,
    min_keep: int = DEFAULT_MIN_KEEP,
) -> tuple[list[dict], dict[str, list[dict]]]:
    """Every scored row with a trace, folded and cut to a fair share of one budget.

    Rows that failed are left out: an agent timeout has no trajectory to reflect
    on, and its absence is already recorded on the rollout. Rows whose trace
    never landed are left out too, for the same reason — but their verdict still
    counted towards the score, so the batch the analyst sees can legitimately be
    smaller than the batch that was measured.

    The budget is shared equally and then the unused part is redistributed, so a
    batch of one short trace and one long one does not cut the long one to half
    the budget while the short one leaves half of its own unspent.
    """
    questions = questions or {}
    ground_truths = ground_truths or {}

    usable = [row for row in rows if row.status == "done" and row.trace is not None]
    if not usable:
        return [], {}

    folded = [build_trajectory(row.trace) for row in usable]
    headers = [
        _header_chars(row, questions.get(row.item_key, ""), ground_truths.get(row.item_key, ""))
        for row in usable
    ]
    shares = allocate_budget(
        [trajectory_chars(traj) + head for traj, head in zip(folded, headers)],
        budget_chars,
    )

    items: list[dict] = []
    ledger: dict[str, list[dict]] = {}
    for row, traj, head, share in zip(usable, folded, headers, shares):
        # The question, both answers and the judge's comment are not part of the
        # trajectory and are not cuttable — they are the frame that makes it
        # readable — so the trajectory's share is what is left after them.
        trimmed, entries = truncate_trajectory(
            traj, max(share - head, min_keep), min_keep=min_keep,
        )
        if entries:
            ledger[row.item_key] = [{"item_key": row.item_key, **entry} for entry in entries]
        items.append(
            analyst_item(
                row,
                trajectory=trimmed,
                question=questions.get(row.item_key, ""),
                ground_truth=ground_truths.get(row.item_key, ""),
            )
        )

    return _drop_until_it_fits(items, budget_chars, ledger)


def _header_chars(row: ResultRow, question: str, ground_truth: str) -> int:
    """The uncuttable frame around one trajectory: task, both answers, verdict."""
    return (
        len(question)
        + len(row.agent_response or "")
        + len(ground_truth)
        + len(row.judge_comment or "")
    )


def item_chars(item: dict) -> int:
    """Everything one item contributes to the prompt."""
    traj = item.get("trajectory")
    size = trajectory_chars(traj) if isinstance(traj, Trajectory) else 0
    return size + sum(
        len(str(item.get(key) or ""))
        for key in ("task_description", "agent_response", "reference_text", "fail_reason")
    )


def _drop_until_it_fits(
    items: list[dict], budget_chars: int, ledger: dict[str, list[dict]],
) -> tuple[list[dict], dict[str, list[dict]]]:
    """The last resort: stop showing whole runs, largest first, and say which.

    Cutting has a floor — a tool call is never shortened, nor the final answer,
    nor the system prompt — so a batch of long trajectories can sit above the
    budget with nothing left that may legitimately be cut. Something has to
    give, and the honest thing to give is a whole run: an analyst told that a
    trajectory was withheld knows what it is missing, whereas one handed eight
    mutilated trajectories does not. The alternative is the request the model
    refuses outright, which is what used to happen every time.

    The item stays in the batch with its question, its answer and the judge's
    verdict — that much still says something, and it keeps the question grouped
    under the analyst call it belonged to on the Part 1 page. Only the run
    itself goes. If even that is not enough, the item is removed entirely; the
    last one standing is never removed, because an empty minibatch is a
    paid-for prompt inviting the model to invent a reason to edit.
    """
    def note(item: dict, size: int) -> None:
        key = str(item["id"])
        ledger.setdefault(key, []).append({
            "item_key": key,
            "span_index": None,
            "field": "trajectory",
            "stage": "dropped_item",
            "before": size,
            "after": 0,
        })

    total = sum(item_chars(item) for item in items)

    shown = [i for i in items if trajectory_chars(i["trajectory"]) > 0]
    while total > budget_chars and shown:
        victim = max(shown, key=lambda item: trajectory_chars(item["trajectory"]))
        size = trajectory_chars(victim["trajectory"])
        victim["trajectory"] = Trajectory()
        victim["dropped"] = True
        victim["n_turns"] = 0
        total -= size
        note(victim, size)
        shown = [i for i in shown if i is not victim]

    # Reaching here means every run has already gone and the questions alone
    # still do not fit, so each survivor is already in the ledger as dropped.
    while total > budget_chars and len(items) > 1:
        victim = max(items, key=item_chars)
        total -= item_chars(victim)
        items = [item for item in items if item is not victim]

    return items, ledger
