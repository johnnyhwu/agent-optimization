"""What a run trains on: which questions, grouped how, split which way.

All of this happens once, in the wizard, before any money is spent — and it
decides what the run *means*. Two properties are load-bearing enough to live in
their own module rather than inside an endpoint:

**A question's identity is composite.** `question_id` is unique per eval set
(the constraint on `questions` says so), and a run can import from several sets.
Two sets holding `q_1` is the ordinary result of downloading a set, editing it
and uploading it again — so keying items by `question_id` alone would merge two
different questions into one training item, and the survivor would carry the
other's gold answer.

**The split is stratified, not shuffled.** The gate accepts a candidate only if
it beats the current skill on validation. If validation happens to draw the
questions the agent already answers correctly, the baseline sits near the
ceiling, every candidate ties, and the run rejects everything for an hour with a
chart that shows nothing wrong. Spreading prior accuracy across both splits is
what stops the default proposal from being a coin flip on that.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Iterable, Sequence

ITEM_KEY_SEP = ":"

DEFAULT_TRAIN_SHARE = 0.7

# Below these a run is refused. On a validation split of three, one question is
# 33 percentage points: the gate would accept and reject on coin flips, and an
# hour of agent calls would buy nothing anyone can act on.
MIN_TRAIN = 8
MIN_VAL = 5

# Below these it runs, with a warning. Workable, but every figure carries a wide
# enough error bar that the developer should know before they start.
WARN_TRAIN = 20
WARN_VAL = 10


@dataclass(frozen=True)
class Candidate:
    """One question the wizard is offering, with what is known about it."""

    item_key: str
    question_id: str
    question: str
    ground_truth_response: str
    ground_truth_reasoning: str
    eval_set_id: uuid.UUID
    eval_set_name: str
    skills: tuple[str, ...] = ()
    # What the picker showed, from the most recent completed runs. `None` means
    # never run — deliberately not 0.0, which would read as "always wrong" and
    # send the question to the wrong end of the difficulty ordering.
    prior_accuracy: float | None = None
    prior_runs: int = 0
    question_pk: uuid.UUID | None = None


def item_key(eval_set_id, question_id: str) -> str:
    """The composite id the algorithm uses for one question.

    Split with `partition`, never `split`: an uploaded `question_id` is
    developer-supplied text and one of them will eventually contain a colon.
    Taking everything after the *first* separator keeps `…:invoice:2024:q7`
    meaning question `invoice:2024:q7`.
    """
    return f"{eval_set_id}{ITEM_KEY_SEP}{question_id}"


def split_item_key(key: str) -> tuple[str, str]:
    """`(eval_set_id, question_id)` — the inverse of `item_key`."""
    prefix, _, question_id = key.partition(ITEM_KEY_SEP)
    return prefix, question_id


def group_by_skill(
    candidates: Iterable[Candidate], *, mode: str,
) -> tuple[dict[str, list[Candidate]], list[Candidate]]:
    """`({skill: [candidate]}, unassignable)` — the wizard's groups, per mode.

    A question with **no** tag goes to the second list in either mode. Guessing
    a group for it would have an analyst reflect on it while editing a skill it
    may have nothing to do with, and the run would look entirely normal while
    learning from it. The wizard shows the bucket, disabled, so the developer
    can go and fix the labels — the only place the answer actually exists.

    A question with **several** tags is where the modes part, and `mode` is a
    required argument because getting it silently wrong is the whole hazard:

      * ``isolated`` excludes it, as this function always did. The run optimises
        one skill's body against the questions in its group, so training
        `billing` on a question tagged `billing` *and* `reporting` attributes to
        `billing` a failure that may belong entirely to the other — and the
        other skill is neither sent to the agent nor editable, so nothing in the
        run can tell the difference.
      * ``routing`` puts it in **every** group it names. That mode optimises
        competing descriptions together and scores each question against all of
        its tags, so a question spanning two is precisely the evidence that says
        where the boundary between their descriptions belongs. Excluding those
        discarded the most informative questions in the set.

    In routing the consequence is that group sizes sum to more than the number
    of questions. That is real and the wizard says so, rather than being hidden
    by picking a group.

    Skills come back sorted; the wizard renders them as a list and one that
    reshuffles between requests is unusable.
    """
    groups: dict[str, list[Candidate]] = {}
    unassignable: list[Candidate] = []
    for candidate in candidates:
        if not candidate.skills or (mode != "routing" and len(candidate.skills) != 1):
            unassignable.append(candidate)
            continue
        for skill in candidate.skills:
            groups.setdefault(skill, []).append(candidate)
    return {name: groups[name] for name in sorted(groups)}, unassignable


def _difficulty(candidate: Candidate) -> tuple:
    """Sort key: known accuracy ascending, then never-run, then by id.

    Never-run questions sort as their own band rather than as 0.0. Folding them
    in with the numbers would pile every unknown at the hard end and hand them
    all to whichever split that end fell into.
    """
    unknown = candidate.prior_accuracy is None
    return (unknown, candidate.prior_accuracy if not unknown else 0.0, candidate.item_key)


def default_split(
    candidates: Sequence[Candidate], *, train_share: float = DEFAULT_TRAIN_SHARE
) -> tuple[list[Candidate], list[Candidate]]:
    """The proposal the wizard opens with: stratified by prior accuracy.

    Questions are ordered by difficulty and then walked, taking one for
    validation whenever the running quota crosses an integer. That interleaves
    the two splits through the whole ordering, so each gets a comparable mix of
    what the agent finds easy and hard — and it is deterministic, which the
    wizard needs because stepping back and forward must not silently discard the
    adjustments already made.
    """
    ordered = sorted(candidates, key=_difficulty)
    if not ordered:
        return [], []

    val_share = 1.0 - train_share
    train: list[Candidate] = []
    val: list[Candidate] = []
    for index, candidate in enumerate(ordered):
        crossed = int((index + 1) * val_share) > int(index * val_share)
        (val if crossed else train).append(candidate)

    # Rounding can leave one side empty on a handful of questions. The size gate
    # refuses a run that small anyway, but this function also renders while the
    # developer is still choosing sources, and `0 validation` on that screen
    # reads as a broken tool rather than as a fact about their data.
    if not val and len(train) > 1:
        val.append(train.pop())
    if not train and len(val) > 1:
        train.append(val.pop(0))
    return train, val


def split_issues(train_keys: Sequence[str], val_keys: Sequence[str]) -> list[dict]:
    """What is wrong with a split: `error` blocks the run, `warning` does not.

    The overlap case is the interesting one. Putting a question in both splits
    is a deliberate feature — with few questions a developer may want one
    counted twice — and it also breaks the gate, because a question the skill
    was just edited *for* is not held-out data. So it warns rather than blocks,
    and names the questions, which is what makes it a decision instead of an
    accident.
    """
    issues: list[dict] = []
    n_train, n_val = len(train_keys), len(val_keys)

    if n_train < MIN_TRAIN:
        issues.append({
            "level": "error", "code": "train_too_small",
            "message": (
                f"the training split has {n_train} questions; at least {MIN_TRAIN} "
                "are needed for a minibatch to say anything about a pattern"
            ),
        })
    elif n_train < WARN_TRAIN:
        issues.append({
            "level": "warning", "code": "train_small",
            "message": (
                f"{n_train} training questions is workable but thin — reflection "
                "generalises from what repeats across a batch"
            ),
        })

    if n_val < MIN_VAL:
        issues.append({
            "level": "error", "code": "val_too_small",
            "message": (
                f"the validation split has {n_val} questions; at least {MIN_VAL} "
                "are needed before an accuracy comparison means anything"
            ),
        })
    elif n_val < WARN_VAL:
        issues.append({
            "level": "warning", "code": "val_small",
            "message": (
                f"with {n_val} validation questions each one moves accuracy by "
                f"{100 / n_val:.0f} points, so the gate will be noisy"
            ),
        })

    overlap = sorted(set(train_keys) & set(val_keys))
    if overlap:
        issues.append({
            "level": "warning", "code": "overlap",
            "item_keys": overlap,
            "message": (
                f"{len(overlap)} question(s) are in both splits. Validation is no "
                "longer held out for those, so the gate will read improvements "
                "that are partly the skill being fitted to them"
            ),
        })
    return issues
