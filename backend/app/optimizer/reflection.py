"""Turning a scored rollout into what the analyst actually reads.

SkillOpt's reflect stage wants trajectories: a list of what the agent did, per
task, formatted into one prompt per minibatch. Its own environments produce
those locally and write them to disk. Ours arrive as Langfuse traces, which
changes two things and only two.

**They have to be bounded before they are formatted.** A trace is as long as the
agent happened to be chatty, and a minibatch is several of them. Left alone, the
analyst prompt for a step is not a fixed cost — it is whatever the worst
trajectory in the batch did, multiplied by the minibatch size, and the first
time anyone notices is when a model refuses the request. So the budget is shared
out here (`allocate_budget`) and each trace is cut to its share
(`truncate_trace`), before a single character of prompt exists.

**What was cut has to be recoverable.** The Part 1 page shows the prompt with
its elisions marked, because "the model did not fix this" and "the model never
saw this" look identical in the output and are opposite problems. The ledger
returned here is what lets the page tell them apart.
"""
from __future__ import annotations

from typing import Mapping, Sequence

from app.integrations.base import Trace
from app.optimizer.store import ResultRow
from app.services.truncation import (
    DEFAULT_MIN_KEEP,
    allocate_budget,
    trace_chars,
    truncate_trace,
)

# Roughly 15k tokens of trajectory per minibatch. Large enough that a handful of
# ordinary traces pass through untouched — the first stage of the cascade is
# "measure, and cut nothing if it fits" — and small enough to leave room for the
# skill itself, which shares the same context.
DEFAULT_REFLECT_BUDGET_CHARS = 60_000


def conversation_from_trace(trace: Trace | None) -> list[dict]:
    """A trace as the `{"type": "tool_call", ...}` records upstream formats.

    Upstream's `fmt_trajectory` already understands this shape — it renders each
    record as an `[action]` / `[obs]` pair — so the mapping is chosen to land on
    a format the vendored code reads natively rather than to invent a new one.

    The tool *name* is carried into the command line because the detector and
    the analyst both need it: a trajectory in which the agent read
    `billing/SKILL.md` and one in which it read nothing look the same once the
    tool name is dropped.
    """
    if trace is None:
        return []
    conversation: list[dict] = []
    for span in trace.spans:
        name = span.tool_name or "step"
        command = span.input or ""
        conversation.append({
            "type": "tool_call",
            "cmd": f"{name}: {command}" if command else name,
            "obs": span.output or "",
        })
    return conversation


def analyst_item(row: ResultRow, *, conversation: list[dict], question: str,
                 ground_truth: str) -> dict:
    """One rollout result in the shape upstream's minibatch formatter expects.

    `hard` is what splits the batch into the failure analyst's group and the
    success analyst's, so it comes from the judge's verdict rather than from the
    score: a partially-correct answer is a failure to learn from, not a success
    to reinforce.

    The gold answer travels as `reference_text` — upstream renders it under
    "Hidden Reference". That is deliberate and it is why the analyst prompts
    forbid copying it: a model cannot explain why an answer was wrong without
    knowing what right looked like, and the defence against memorisation is the
    held-out split plus the leak check on the diff, not withholding the evidence.
    """
    return {
        "id": row.item_key,
        "hard": 1.0 if row.verdict == "correct" else 0.0,
        "soft": float(row.judge_score or 0.0),
        "task_description": question,
        "task_type": "",
        "reference_text": ground_truth,
        "fail_reason": (row.judge_comment or "") if row.verdict != "correct" else "",
        "n_turns": len(conversation),
        "agent_response": row.agent_response or "",
        "conversation": conversation,
    }


def build_analyst_items(
    rows: Sequence[ResultRow],
    *,
    questions: Mapping[str, str] | None = None,
    ground_truths: Mapping[str, str] | None = None,
    budget_chars: int = DEFAULT_REFLECT_BUDGET_CHARS,
    min_keep: int = DEFAULT_MIN_KEEP,
) -> tuple[list[dict], dict[str, list[dict]]]:
    """Every scored row with a trace, truncated to a fair share of one budget.

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

    shares = allocate_budget([trace_chars(row.trace) for row in usable], budget_chars)

    items: list[dict] = []
    ledger: dict[str, list[dict]] = {}
    for row, share in zip(usable, shares):
        trimmed, entries = truncate_trace(row.trace, share, min_keep=min_keep)
        if entries:
            ledger[row.item_key] = [{"item_key": row.item_key, **entry} for entry in entries]
        items.append(
            analyst_item(
                row,
                conversation=conversation_from_trace(trimmed),
                question=questions.get(row.item_key, ""),
                ground_truth=ground_truths.get(row.item_key, ""),
            )
        )
    return items, ledger
