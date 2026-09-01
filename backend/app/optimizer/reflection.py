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

from dataclasses import replace
from typing import Mapping, Sequence

from app.integrations.base import Trace
from app.optimizer.store import ResultRow
from app.optimizer.trajectory import (
    Trajectory,
    build_trajectory,
    conversation_chars,
    preamble_chars,
    shared_preamble,
    truncate_trajectory,
)
from app.services.truncation import DEFAULT_MIN_KEEP, allocate_budget

# The trajectory half of one analyst prompt: everything the batch shares,
# counted once, plus each question's own conversation and frame.
#
# 200,000 characters is roughly 50k-80k tokens depending on how dense the text
# is, which leaves a 100k-token model room for the skill in front of it and the
# analyst's own answer — up to 16k tokens — behind it. The wizard states that
# rule beside the field, because the right number depends on the model the run
# is pointed at and on the language its traces are in: CJK text costs several
# times more tokens per character than this assumes.
#
# The history is worth knowing before adjusting it. It was 60,000 when every
# span carried an uncuttable copy of the system prompt — a budget no cascade
# could meet, so every prompt went out oversized regardless. Folding the
# repetition away (`trajectory.py`) made it reachable, and hoisting what a
# minibatch shares made it *buy* far more than it used to: with an 8k-token
# system prompt and eight questions, seven copies of it — some 56k tokens —
# used to be inside this figure and are now outside it entirely.
DEFAULT_REFLECT_BUDGET_CHARS = 200_000


def conversation_from_trace(trace: Trace | None) -> Trajectory:
    """A trace as one conversation. See `app/optimizer/trajectory.py`."""
    return build_trajectory(trace)


def analyst_item(row: ResultRow, *, trajectory: Trajectory, question: str,
                 ground_truth: str, mode: str = "isolated",
                 gt_skills: Sequence[str] = ()) -> dict:
    """One rollout result in the shape the minibatch formatter expects.

    `hard` is what splits the batch into the failure analyst's group and the
    success analyst's (`update._reflect`), so it has to be the thing the run is
    optimising — otherwise the analysts are shown one definition of failure
    while the gate enforces another, and the disagreement is silent.

      * **isolated** optimises the body against the answers, so `hard` is the
        judge's verdict rather than its score: a partially-correct answer is a
        failure to learn from, not a success to reinforce.
      * **routing** optimises the description against which skill was opened,
        so `hard` is whether this question reached exactly the skills it was
        tagged with. A question answered correctly *despite* opening the wrong
        skill is a routing failure — sent to the success analyst it would be
        presented under "These questions were answered correctly, so the routing
        worked", which is the opposite of what happened. And a wrong answer that
        routed correctly is a routing *success*: the body is at fault, a
        description edit cannot fix it, and inviting one produces exactly the
        narrowing the gate exists to refuse.

    A routing question that could not be measured — no trajectory, or no tags —
    keeps the judge's verdict. It has to land in some group, and the judge is
    the only signal there is; `routing_scores` leaves it out of the score, so it
    cannot move the gate either way.

    `fail_reason` is the judge's own comment — the same sentence the Evaluation
    page shows under "Judge" — in isolated mode, because an analyst told only
    "this failed" cannot tell a wrong answer from one graded strictly. In
    routing mode it says which skills were wanted and which were opened, which
    is the whole of what went wrong.

    `gt_skills` and `skills_read` also travel as fields of their own rather than
    being left for the model to read out of the trajectory. The evidence for
    them lives in tool results, and `trajectory.truncate_trajectory` cuts those
    first when a minibatch is over budget — so on the long trajectories where a
    routing failure is most likely to hide, the proof of it is the first thing
    to go.

    The gold answer travels as `reference_text`, rendered under "Ground-truth
    Response". Showing it is deliberate and it is why the analyst prompts forbid
    copying it: a model cannot explain why an answer was wrong without knowing
    what right looked like, and the defence against memorisation is the held-out
    split plus the leak check on the diff, not withholding the evidence.
    """
    answered_well = row.verdict == "correct"
    wanted = sorted(gt_skills)
    read = sorted(row.skills_read) if row.skills_read is not None else None

    routed_well = None
    if mode == "routing" and wanted and read is not None:
        routed_well = set(read) == set(wanted)

    if routed_well is None:
        hard = 1.0 if answered_well else 0.0
        fail_reason = (row.judge_comment or "") if not answered_well else ""
    else:
        hard = 1.0 if routed_well else 0.0
        fail_reason = "" if routed_well else (
            f"the question is tagged {', '.join(wanted) or '(nothing)'} but the agent "
            f"read {', '.join(read) or 'no skill at all'}"
        )

    return {
        "id": row.item_key,
        "hard": hard,
        "soft": float(row.judge_score or 0.0),
        "task_description": question,
        "reference_text": ground_truth,
        "fail_reason": fail_reason,
        "gt_skills": wanted,
        "skills_read": read,
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
    mode: str = "isolated",
    gt_skills: Mapping[str, Sequence[str]] | None = None,
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

    What the batch shares is charged once, not once per trajectory. The system
    prompt carries the skill and, on a real agent, runs to thousands of tokens;
    every trajectory in a step was answered under the same one, so the prompt
    prints it once (`analyst.format_minibatch`) and the budget is spent the same
    way. Counting it per item instead would cut real evidence to make room for
    copies that are never sent.
    """
    questions = questions or {}
    ground_truths = ground_truths or {}
    gt_skills = gt_skills or {}

    usable = [row for row in rows if row.status == "done" and row.trace is not None]
    if not usable:
        return [], {}

    # The rollout already folded this trace and hung it on the row
    # (`adapter.run_rollout`), so the common path costs nothing here. The
    # fallback is not dead code: a row built from a trace alone — a replay, a
    # resumed step, every test in `test_optimizer_trajectory.py` — has no
    # trajectory to inherit, and folding is cheaper than requiring one.
    folded = [row.trajectory or build_trajectory(row.trace) for row in usable]
    headers = [
        _header_chars(row, questions.get(row.item_key, ""), ground_truths.get(row.item_key, ""))
        for row in usable
    ]

    # Charged once when it is common to the batch, per item when it is not.
    shared = shared_preamble(folded)
    spent_on_setup = preamble_chars(shared) if shared else 0
    own_setup = [0 if shared else preamble_chars(traj) for traj in folded]
    for_conversations = max(budget_chars - spent_on_setup, min_keep * len(usable))

    shares = allocate_budget(
        [
            conversation_chars(traj) + head + setup
            for traj, head, setup in zip(folded, headers, own_setup)
        ],
        for_conversations,
    )

    items: list[dict] = []
    ledger: dict[str, list[dict]] = {}
    for row, traj, head, setup, share in zip(usable, folded, headers, own_setup, shares):
        # The question, both answers and the judge's comment are not part of the
        # trajectory and are not cuttable — they are the frame that makes it
        # readable — so the conversation's share is what is left after them.
        trimmed, entries = truncate_trajectory(
            traj, max(share - head - setup, min_keep), min_keep=min_keep,
        )
        if entries:
            ledger[row.item_key] = [{"item_key": row.item_key, **entry} for entry in entries]
        items.append(
            analyst_item(
                row,
                trajectory=trimmed,
                question=questions.get(row.item_key, ""),
                ground_truth=ground_truths.get(row.item_key, ""),
                mode=mode,
                gt_skills=gt_skills.get(row.item_key, ()),
            )
        )

    return _drop_until_it_fits(items, for_conversations, ledger)


def build_routing_items(
    rows: Sequence[ResultRow],
    *,
    questions: Mapping[str, str] | None = None,
    ground_truths: Mapping[str, str] | None = None,
    gt_skills: Mapping[str, Sequence[str]] | None = None,
) -> list[dict]:
    """The same items, for a mode that sends no trajectories.

    `build_analyst_items` folds every trace, shares a character budget out
    between them, cuts each to its share and — when the batch still does not
    fit — drops whole questions. All of that is care taken over text routing no
    longer sends: the analyst reads a digest of roughly a line per question, so
    the budget cannot bind, and a question dropped to satisfy it would be a
    question missing from a confusion matrix that reports its own totals. The
    counts would be right and the rows would not.

    Two differences beyond the missing budget, and both are about not throwing
    away evidence this mode can use:

    **A row whose trace never landed is kept.** `build_analyst_items` drops it —
    correctly, since a trajectory is the whole of what it was going to
    contribute. Here the question and its tags survive the missing trace, and
    the digest reports it as *not measured* rather than as a miss. Dropping it
    would leave the analyst quietly reasoning about a smaller batch than the one
    the gate scored.

    **The preamble travels without the conversation.** The agent's system prompt
    is printed once above the matrix, because a routing failure caused by the
    agent's own instructions is indistinguishable from one caused by a bad
    description until you can read them. So each item carries a `Trajectory`
    holding the setup and no turns — enough for `analyst.run_analyst_minibatch`
    to fold the setups together, and nothing that would end up in the prompt.
    """
    questions = questions or {}
    ground_truths = ground_truths or {}
    gt_skills = gt_skills or {}

    items: list[dict] = []
    for row in rows:
        if row.status != "done":
            continue
        folded = row.trajectory or build_trajectory(row.trace)
        items.append(
            analyst_item(
                row,
                trajectory=Trajectory(tools=folded.tools, system_prompt=folded.system_prompt),
                question=questions.get(row.item_key, ""),
                ground_truth=ground_truths.get(row.item_key, ""),
                mode="routing",
                gt_skills=gt_skills.get(row.item_key, ()),
            )
        )
    return items


def _header_chars(row: ResultRow, question: str, ground_truth: str) -> int:
    """The uncuttable frame around one trajectory: task, both answers, verdict."""
    return (
        len(question)
        + len(row.agent_response or "")
        + len(ground_truth)
        + len(row.judge_comment or "")
    )


def item_chars(item: dict) -> int:
    """What one item adds to the prompt, over what the batch already shares.

    The conversation and the frame around it. Not the preamble: where it is
    common to the batch it is printed once no matter how many items there are,
    so charging it here would make dropping an item look like it saved several
    thousand characters it never cost.
    """
    traj = item.get("trajectory")
    size = conversation_chars(traj) if isinstance(traj, Trajectory) else 0
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

    shown = [i for i in items if conversation_chars(i["trajectory"]) > 0]
    while total > budget_chars and shown:
        victim = max(shown, key=lambda item: conversation_chars(item["trajectory"]))
        size = conversation_chars(victim["trajectory"])
        # The setup is kept: it is shared with the rest of the batch and is
        # printed once whatever happens to this item.
        victim["trajectory"] = replace(victim["trajectory"], turns=[])
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
