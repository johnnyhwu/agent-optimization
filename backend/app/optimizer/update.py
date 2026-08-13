"""One step's gradient: reflect over minibatches, aggregate, clip, apply.

Everything that decides *what* to change is SkillOpt's and lives in `vendor/`.
This module is the orchestration around it, and it exists rather than calling
upstream's `run_minibatch_reflect` for two reasons, both of which are about what
a developer can see afterwards:

**Checkpointing.** Upstream resumes a step by looking for
`minibatch_fail_003.json` on disk. Ours is a web application whose checkpoint is
a database row, and a step is the granularity — so the file dance would be
bookkeeping for a mechanism we do not use.

**The record.** `optimization_minibatches` stores the exact system and user
prompt that went to the analyst, its raw answer, and what was truncated on the
way in. That is the "visible gradient" the Part 1 page is built on, and it is
the difference between a developer being able to say "the model had a bad idea"
and only being able to say "the skill got worse". Upstream builds those prompts
inside the analyst call and discards them, so the only way to have them is to
capture at the seam — which is exactly what `_Recorder` does. Rebuilding them
afterwards would produce a page that agrees with itself and drifts from what was
actually sent.

The splitting, shuffling, prompt construction, merging and ranking are all still
upstream's functions, called directly.

**This module is synchronous**, because everything it calls is. The engine runs
it in `asyncio.to_thread`, and the analyst calls fan out on a thread pool from
there — see `vendor/_model.py` for why the seam is a module-level global rather
than an argument.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from app.integrations.base import OptimizerClient
from app.optimizer.skillio import build_protection, render_skill
from app.optimizer.vendor._model import use_optimizer
from app.optimizer.vendor.aggregate import merge_patches
from app.optimizer.vendor.clip import rank_and_select
from app.optimizer.vendor.reflect import (
    _split_minibatches,
    _shuffle_for_minibatch,
    run_error_analyst_minibatch,
    run_success_analyst_minibatch,
)
from app.optimizer.vendor.skill import apply_patch_with_report
from app.optimizer.vendor.update_modes import get_payload_items

log = logging.getLogger(__name__)


@dataclass
class MinibatchRecord:
    """One analyst call, as `optimization_minibatches` stores it."""

    minibatch_no: int
    source_type: str  # failure | success
    n_items: int
    item_keys: list[str]
    prompt_system: str
    prompt_user: str
    raw_output: dict | None
    truncation: list[dict]
    chars_before: int
    chars_after: int
    error: str | None
    duration_ms: int


@dataclass
class UpdateOutcome:
    """A candidate skill, and everything the step row and Part 1 report about it."""

    files: dict[str, str]
    patch: dict
    reports: list[dict]
    minibatches: list[MinibatchRecord]
    n_edits_merged: int
    n_edits_ranked: int
    n_edits_applied: int
    n_edits_skipped: int
    edit_summary: str
    tokens: dict = field(default_factory=dict)


class _Recorder:
    """An `OptimizerClient` that remembers each call, by the thread it came in on.

    Attribution has to be by thread, not by arrival order: the analysts run on a
    pool, so the second call to return is not reliably the second one made.
    Pairing a record with whichever prompt happened to finish next would be
    wrong about half the time, and wrong in a way that still reads perfectly
    plausibly on screen.
    """

    def __init__(self, inner: OptimizerClient) -> None:
        self._inner = inner
        self._lock = threading.Lock()
        self._by_thread: dict[int, list[dict]] = {}
        self.tokens: dict[str, int] = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}

    @property
    def model_name(self) -> str:
        return getattr(self._inner, "model_name", "optimizer")

    def chat_optimizer(self, system, user, max_completion_tokens=16384, retries=3,
                       stage="optimizer", timeout=None):
        started = time.monotonic()
        call: dict[str, Any] = {"stage": stage, "system": system, "user": user}
        try:
            text, usage = self._inner.chat_optimizer(
                system=system, user=user, max_completion_tokens=max_completion_tokens,
                retries=retries, stage=stage, timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001 - recorded, then re-raised
            call["error"] = f"{type(exc).__name__}: {exc}"
            call["duration_ms"] = int((time.monotonic() - started) * 1000)
            self._store(call)
            raise
        call["output"] = text
        call["duration_ms"] = int((time.monotonic() - started) * 1000)
        self._store(call, usage)
        return text, usage

    def _store(self, call: dict, usage: Mapping[str, int] | None = None) -> None:
        ident = threading.get_ident()
        with self._lock:
            self._by_thread.setdefault(ident, []).append(call)
            if usage:
                for key in self.tokens:
                    self.tokens[key] += int(usage.get(key, 0) or 0)

    def take(self) -> list[dict]:
        """This thread's calls since it last asked, and clear them."""
        ident = threading.get_ident()
        with self._lock:
            return self._by_thread.pop(ident, [])

    def reset(self) -> None:
        """Drop every buffer. Called between stages so pool threads do not leak."""
        with self._lock:
            self._by_thread.clear()


def run_update_stage(
    *,
    files: Mapping[str, str],
    skill_dir: str,
    mode: str,
    items: Sequence[dict],
    client: OptimizerClient,
    edit_budget: int,
    minibatch_size: int = 8,
    analyst_workers: int = 4,
    merge_batch_size: int = 8,
    failure_only: bool = False,
    seed: int | None = None,
    update_mode: str = "patch",
    truncation_by_item: Mapping[str, list[dict]] | None = None,
) -> UpdateOutcome:
    """Everything between a scored training batch and a candidate skill."""
    files = dict(files)
    truncation_by_item = truncation_by_item or {}

    if not items:
        # A step whose whole batch failed, or whose traces never landed. There is
        # nothing to reflect on, and an empty minibatch would be a paid-for
        # prompt inviting the model to invent a reason to edit.
        return UpdateOutcome(
            files=files, patch={"reasoning": "no trajectories to reflect on", "edits": []},
            reports=[], minibatches=[], n_edits_merged=0, n_edits_ranked=0,
            n_edits_applied=0, n_edits_skipped=0,
            edit_summary="no trajectories to reflect on",
            tokens={"calls": 0, "prompt_tokens": 0, "completion_tokens": 0},
        )

    skill_content = render_skill(files, skill_dir)
    recorder = _Recorder(client)

    with use_optimizer(recorder):
        records, patches = _reflect(
            skill_content=skill_content, mode=mode, items=items, recorder=recorder,
            edit_budget=edit_budget, minibatch_size=minibatch_size,
            analyst_workers=analyst_workers, failure_only=failure_only, seed=seed,
            update_mode=update_mode, truncation_by_item=truncation_by_item,
        )
        recorder.reset()

        failure_patches = [p["patch"] for p in patches if p.get("source_type") == "failure"]
        success_patches = [p["patch"] for p in patches if p.get("source_type") == "success"]

        merged = merge_patches(
            skill_content, failure_patches, success_patches,
            batch_size=merge_batch_size, verbose=False, workers=analyst_workers,
            update_mode=update_mode,
        )
        n_merged = len(get_payload_items(merged, update_mode))

        selected = rank_and_select(
            skill_content, merged, edit_budget, update_mode=update_mode
        )
        recorder.reset()

    n_ranked = len(get_payload_items(selected, update_mode))

    protection = build_protection(files, skill_dir, mode)
    candidate, reports = apply_patch_with_report(
        files, selected, skill_dir=skill_dir, protection=protection
    )
    applied = sum(1 for r in reports if r.get("status", "").startswith("applied"))

    return UpdateOutcome(
        files=candidate,
        patch=selected,
        reports=reports,
        minibatches=records,
        n_edits_merged=n_merged,
        n_edits_ranked=n_ranked,
        n_edits_applied=applied,
        n_edits_skipped=len(reports) - applied,
        edit_summary=str(selected.get("reasoning") or "").strip(),
        tokens=dict(recorder.tokens),
    )


def _reflect(
    *, skill_content, mode, items, recorder, edit_budget, minibatch_size,
    analyst_workers, failure_only, seed, update_mode, truncation_by_item,
) -> tuple[list[MinibatchRecord], list[dict]]:
    """Split into minibatches and run one analyst call per group, in parallel.

    The split, the shuffle and the analyst calls are upstream's. What is added is
    the numbering — minibatches are numbered in submission order, not completion
    order, so the number on screen is stable across reruns of the same step —
    and the record built from the recorder's view of each call.
    """
    failures = [i for i in items if not float(i.get("hard") or 0.0)]
    successes = [] if failure_only else [i for i in items if float(i.get("hard") or 0.0)]

    failures = _shuffle_for_minibatch(failures, seed)
    successes = _shuffle_for_minibatch(successes, None if seed is None else seed + 1)

    groups: list[tuple[str, list[dict]]] = (
        [("failure", batch) for batch in _split_minibatches(failures, minibatch_size)]
        + [("success", batch) for batch in _split_minibatches(successes, minibatch_size)]
    )

    def analyse(index: int, source_type: str, batch: list[dict]) -> tuple[MinibatchRecord, dict | None]:
        recorder.take()  # clear anything this pool thread carried from a previous batch
        started = time.monotonic()
        error: str | None = None
        patch: dict | None = None
        try:
            runner = (
                run_error_analyst_minibatch if source_type == "failure"
                else run_success_analyst_minibatch
            )
            patch = runner(
                skill_content, batch, None,
                edit_budget=edit_budget,
                system_prompt=_analyst_prompt(source_type, mode),
                update_mode=update_mode,
            )
        except Exception as exc:  # noqa: BLE001 - one batch must not end the step
            error = f"{type(exc).__name__}: {exc}"
            log.warning("analyst minibatch %s failed: %s", index, error)

        calls = recorder.take()
        call = calls[-1] if calls else {}
        if error is None and call.get("error"):
            # Upstream swallows the exception and returns None, which on its own
            # is indistinguishable from "the model had nothing to say".
            error = call["error"]

        chars_cut, entries = _truncation_for(batch, truncation_by_item)
        chars_after = _batch_chars(batch)
        record = MinibatchRecord(
            minibatch_no=index,
            source_type=source_type,
            n_items=len(batch),
            item_keys=[str(i["id"]) for i in batch],
            prompt_system=call.get("system", ""),
            prompt_user=call.get("user", ""),
            raw_output=patch,
            truncation=entries,
            # One arrow, one quantity: what this batch would have been, and what
            # it actually was. Anything else and the two ends of "41,200 →
            # 12,000" are measuring different things.
            chars_before=chars_after + chars_cut,
            chars_after=chars_after,
            error=error,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return record, patch

    results: list[tuple[MinibatchRecord, dict | None]] = [None] * len(groups)  # type: ignore[list-item]
    with ThreadPoolExecutor(max_workers=max(analyst_workers, 1)) as pool:
        futures = {
            pool.submit(analyse, index, source_type, batch): index
            for index, (source_type, batch) in enumerate(groups)
        }
        for future in futures:
            results[futures[future]] = future.result()

    records = [record for record, _ in results]
    patches = [patch for _, patch in results if patch and patch.get("patch")]
    return records, patches


def _analyst_prompt(source_type: str, mode: str) -> str:
    """The mode's own analyst prompt, loaded through upstream's resolver.

    Upstream keys the override on the environment; here it is the optimization
    mode, because that — not the benchmark — is what changes the question being
    asked. Falling through to the generic prompt would ask a routing run to
    rewrite a body it is forbidden to touch.
    """
    from app.optimizer.vendor.prompts import load_prompt

    name = "analyst_error" if source_type == "failure" else "analyst_success"
    return load_prompt(name, mode)


def _batch_chars(batch: Sequence[dict]) -> int:
    """How much text this minibatch carries, as the analyst received it."""
    return sum(
        len(str(event.get("obs", ""))) + len(str(event.get("cmd", "")))
        for item in batch for event in item.get("conversation", [])
    )


def _truncation_for(
    batch: Sequence[dict], truncation_by_item: Mapping[str, list[dict]]
) -> tuple[int, list[dict]]:
    """This minibatch's slice of the ledger, and how many characters it lost.

    The ledger is per item and the display is per minibatch, so the join happens
    here — otherwise Part 1 would warn about truncation on batches that had none.

    The count returned is what was *cut*, not the original size of the cut
    slots. A ledger entry describes one span field, so summing its `before`
    gives the size of the touched parts alone — a number with no relationship to
    the size of the batch, and one that comes out smaller than the batch's
    current size whenever the untouched turns outweigh the trimmed ones.
    """
    entries: list[dict] = []
    cut = 0
    for item in batch:
        item_entries = truncation_by_item.get(str(item["id"]), [])
        entries.extend(item_entries)
        cut += sum(
            int(entry.get("before", 0)) - int(entry.get("after", 0))
            for entry in item_entries
        )
    return cut, entries
