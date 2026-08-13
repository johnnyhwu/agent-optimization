"""The epoch boundary: what the run learns by looking across two epochs.

Everything else in this feature looks at one step. Upstream calls this the
**slow update**, and it is the only pass with a longer memory: at the end of an
epoch it compares the *same* samples under the previous epoch's skill and under
this one's, and writes free-form guidance into a protected block of `SKILL.md`
that step-level analysts may not edit.

**The comparison set is the validation split**, and that was the one design
decision this port had to make for itself. Upstream re-rolls a fixed sample of
twenty tasks at each boundary; this loop cannot, because a training minibatch is
a different draw of questions every step, and comparing two different sets of
questions would attribute the difference between the questions to the difference
between the skills — which is the one thing this pass exists to measure. The
validation split is the only set answered under every skill this run produces,
and it is already rolled out at every step, so the comparison costs no extra
agent calls.

**This module is synchronous**, like `update.py` and for the same reason: the
vendored code it calls is. The engine runs it in `asyncio.to_thread`.

Both passes here are off unless the run's config turns them on, and both are
best-effort: they are enrichments on top of a run whose agent calls are already
paid for, so a failure is logged and the run carries on with what it had.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Mapping, Sequence

from app.integrations.base import OptimizerClient
from app.optimizer.vendor._model import use_optimizer
from app.optimizer.vendor.meta_skill import run_meta_skill
from app.optimizer.vendor.skill import entry_point_for
from app.optimizer.vendor.slow_update import (
    build_comparison_pairs,
    replace_slow_update_field,
)

log = logging.getLogger(__name__)


@dataclass
class EpochOutcome:
    """What one boundary produced. Every field may be empty; nothing here is required."""

    files: dict[str, str]
    slow_update_text: str = ""
    meta_skill_text: str = ""
    reasoning: str = ""
    n_improved: int = 0
    n_regressed: int = 0
    n_persistent_fail: int = 0
    changed: bool = False


def _categories(pairs: Sequence[dict]) -> dict[str, int]:
    counts = {"improved": 0, "regressed": 0, "persistent_fail": 0, "stable_success": 0}
    for pair in pairs:
        counts[pair["category"]] = counts.get(pair["category"], 0) + 1
    return counts


def run_epoch_boundary(
    *,
    files: Mapping[str, str],
    prev_files: Mapping[str, str],
    skill_dir: str,
    items: Sequence[dict],
    results_prev: Sequence[dict],
    results_curr: Sequence[dict],
    client: OptimizerClient,
    prev_slow_update_text: str = "",
    prev_meta_skill_text: str = "",
    slow_update: bool = False,
    meta_skill: bool = False,
) -> EpochOutcome:
    """Compare two epochs and fold what it learns back into the skill.

    Returns the skill unchanged when there is nothing to say — no results on
    either side, both passes disabled, or the optimizer declining to answer.
    """
    outcome = EpochOutcome(files=dict(files))
    # A short-circuit, not a correctness guard — the two blocks below check their
    # own switch, so removing this changes no output. It is here because
    # `use_optimizer` takes a process-wide lock (see `vendor/_model.py`), and a
    # run with both passes off should not serialise itself against every other
    # run to do nothing.
    if not (slow_update or meta_skill):
        return outcome
    if not results_prev or not results_curr:
        # One side of the comparison never ran. This is reachable on a resumed
        # run whose earlier epoch was executed by a process that is gone.
        log.info("skipping the epoch boundary: one side has no validation results")
        return outcome

    pairs = build_comparison_pairs(list(results_prev), list(results_curr), list(items))
    counts = _categories(pairs)
    outcome.n_improved = counts["improved"]
    outcome.n_regressed = counts["regressed"]
    outcome.n_persistent_fail = counts["persistent_fail"]

    entry = entry_point_for(skill_dir)
    current = outcome.files.get(entry, "")
    previous = dict(prev_files).get(entry, "")

    with use_optimizer(client):
        if slow_update:
            # Import here rather than at module scope: `run_slow_update` reads
            # the seam at call time, and keeping the import beside the call
            # makes the vendored boundary obvious at the one place it matters.
            from app.optimizer.vendor.slow_update import run_slow_update

            result = run_slow_update(
                current,
                list(results_prev),
                list(results_curr),
                list(items),
                prev_skill=previous,
                prev_slow_update_content=prev_slow_update_text,
                comparison_pairs=pairs,
            )
            if result and result.get("slow_update_content"):
                text = result["slow_update_content"]
                # `replace_slow_update_field` strips every existing block and
                # appends exactly one, so it creates the block on the first
                # boundary as well as replacing it on later ones. The initial
                # skill is left alone deliberately: a run with this pass off
                # never carries markers it does not use, and the block's first
                # appearance is attributable to the boundary that wrote it.
                outcome.files[entry] = replace_slow_update_field(current, text)
                outcome.slow_update_text = text
                outcome.reasoning = result.get("reasoning", "")
                outcome.changed = True

        if meta_skill:
            result = run_meta_skill(
                previous, current, pairs,
                prev_meta_skill_content=prev_meta_skill_text,
            )
            if result and result.get("meta_skill_content"):
                # Optimizer-side memory: it is shown to the *analyst* on later
                # steps and never written into the skill, which is why it does
                # not set `changed`.
                outcome.meta_skill_text = result["meta_skill_content"]

    return outcome
