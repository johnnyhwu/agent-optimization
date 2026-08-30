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
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from app.integrations.base import OptimizerClient
from app.optimizer.analyst import run_analyst_minibatch
from app.optimizer.skillio import build_protection, render_competing_skills, render_skill
from app.optimizer.trajectory import (
    Trajectory,
    conversation_chars,
    preamble_chars,
    shared_preamble,
)
from app.optimizer.vendor._model import use_optimizer
from app.optimizer.vendor.aggregate import merge_patches
from app.optimizer.vendor.clip import rank_and_select
from app.optimizer.vendor.reflect import (
    _split_minibatches,
    _shuffle_for_minibatch,
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
class StageCallRecord:
    """One post-analyst optimizer call, as `optimization_stage_calls` stores it.

    Merge and ranking decide which of the analysts' edits survive, and until
    these were recorded the page could show what was asked for and what was
    applied with nothing in between — so "which stage lost my edit?" was
    unanswerable from the run itself.
    """

    seq: int
    stage: str  # merge_failure | merge_success | merge_final | ranking
    level: int | None
    prompt_system: str
    prompt_user: str
    output: dict | None
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
    stage_calls: list[StageCallRecord] = field(default_factory=list)
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
        self._next_seq = 0
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
            call["seq"] = self._next_seq
            self._next_seq += 1
            self._by_thread.setdefault(ident, []).append(call)
            if usage:
                for key in self.tokens:
                    self.tokens[key] += int(usage.get(key, 0) or 0)

    def take(self) -> list[dict]:
        """This thread's calls since it last asked, and clear them."""
        ident = threading.get_ident()
        with self._lock:
            return self._by_thread.pop(ident, [])

    def drain(self) -> list[dict]:
        """Every thread's calls, in the order they were made, and clear them.

        `take` is per-thread because an analyst has to be paired with *its own*
        prompt. Merge and ranking have the opposite shape: they fan out over a
        pool and the step wants all of them, so the sequence stamped at call
        time is what puts a hierarchical merge back in order.
        """
        with self._lock:
            calls = [call for bucket in self._by_thread.values() for call in bucket]
            self._by_thread.clear()
        return sorted(calls, key=lambda call: call.get("seq", 0))

    def reset(self) -> None:
        """Drop every buffer. Called between stages so pool threads do not leak."""
        with self._lock:
            self._by_thread.clear()


def run_update_stage(
    *,
    files: Mapping[str, str],
    # One skill directory, or several when a routing run is optimising competing
    # descriptions together.
    skill_dir: str | Sequence[str],
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
    # Every *other* skill on the agent, for routing runs. The rollout already
    # sends these to the agent (they are what the description competes against);
    # this is what lets the analyst see the same field. `None` for isolated —
    # and for a routing run resumed from before this existed, which must keep
    # working rather than crash.
    context_files: Mapping[str, str] | None = None,
    # Optimizer-side memory from the last epoch boundary. Empty unless the run
    # turned `meta_skill` on, in which case the analyst is shown what the
    # previous epoch taught the optimizer about its own editing.
    meta_skill_context: str = "",
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

    dirs = [skill_dir] if isinstance(skill_dir, str) else list(skill_dir)
    skill_content = render_skill(files, dirs)
    # Analyst-only, deliberately. Merge and ranking open with the same
    # "## Current Skill" section, so folding this into `skill_content` would
    # have been a one-word change — and would then pay for the whole menu twice
    # more per step, on the largest model in the run, in two stages that combine
    # and choose among edits rather than making any routing judgement.
    #
    # Gated on the mode here rather than at the call site: isolated sends one
    # skill to the agent, so there is no choice for a menu to inform and showing
    # one would have the analyst weighing alternatives the agent was never
    # offered. That is a property of the mode, so it holds wherever the caller
    # is, and it keeps an isolated prompt byte-identical to what it was.
    competing = (
        render_competing_skills({
            path: text for path, text in context_files.items()
            if not any(path == d or path.startswith(f"{d}/") for d in dirs)
        })
        if context_files and mode == "routing" else ""
    )
    recorder = _Recorder(client)

    with use_optimizer(recorder):
        records, patches = _reflect(
            skill_content=skill_content, mode=mode, items=items, recorder=recorder,
            competing_skills=competing,
            edit_budget=edit_budget, minibatch_size=minibatch_size,
            analyst_workers=analyst_workers, failure_only=failure_only, seed=seed,
            update_mode=update_mode, truncation_by_item=truncation_by_item,
            meta_skill_context=meta_skill_context,
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
        # Everything merge and ranking asked, kept. It used to be dropped here,
        # which left the page able to show what each analyst proposed and what
        # the skill ended up with — and nothing about the stages in between,
        # where an edit is most likely to have been lost.
        stage_calls = _stage_calls(recorder.drain(), update_mode)

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
        stage_calls=stage_calls,
        tokens=dict(recorder.tokens),
    )


# The order the pipeline runs them in, which is also the order they are read in.
_STAGE_ORDER = ["merge_failure", "merge_success", "merge_final", "ranking", "merge"]


def _stage_calls(calls: list[dict], update_mode: str) -> list[StageCallRecord]:
    """The drained merge/ranking calls, named and put back in pipeline order.

    Which merge a call belongs to is read off the system prompt it was sent
    with. `merge_patches` runs all three phases behind one function and labels
    every one of them `stage="merge"`, and the alternative to matching the
    prompt is re-implementing upstream's phase structure out here — a copy that
    would drift from the thing it describes. The prompts are separate files, so
    the match is exact.
    """
    known = {
        _merge_prompt(name, update_mode): f"merge_{name}"
        for name in ("failure", "success", "final")
    }
    records: list[StageCallRecord] = []
    for call in calls:
        raw_stage = call.get("stage", "")
        if raw_stage == "analyst":
            continue  # a straggler from a pool thread; the analyst has its own record
        stage = "ranking" if raw_stage == "ranking" else known.get(call.get("system", ""), raw_stage)
        output, error = _parsed_output(call)
        records.append(StageCallRecord(
            seq=int(call.get("seq", 0)),
            stage=stage,
            level=_merge_level(call.get("user", "")),
            prompt_system=call.get("system", ""),
            prompt_user=call.get("user", ""),
            output=output,
            error=error,
            duration_ms=int(call.get("duration_ms", 0)),
        ))

    def rank(record: StageCallRecord) -> tuple[int, int, int]:
        order = _STAGE_ORDER.index(record.stage) if record.stage in _STAGE_ORDER else len(_STAGE_ORDER)
        return (order, record.level or 0, record.seq)

    ordered = sorted(records, key=rank)
    for position, record in enumerate(ordered):
        record.seq = position
    return ordered


def _merge_prompt(name: str, update_mode: str) -> str:
    """The system prompt `aggregate.merge_patches` would load for this mode."""
    from app.optimizer.vendor.prompts import load_prompt
    from app.optimizer.vendor.update_modes import (
        is_full_rewrite_minibatch_mode,
        is_rewrite_mode,
        normalize_update_mode,
    )

    mode = normalize_update_mode(update_mode)
    if is_full_rewrite_minibatch_mode(mode):
        suffix = "_full_rewrite"
    elif is_rewrite_mode(mode):
        suffix = "_rewrite"
    else:
        suffix = ""
    try:
        return load_prompt(f"merge_{name}{suffix}")
    except FileNotFoundError:
        return ""


def _parsed_output(call: dict) -> tuple[dict | None, str | None]:
    """The answer as parsed, and why there isn't one when there isn't.

    A stage that could not be parsed did not merely fail to be displayed — its
    patch was discarded and upstream fell back to concatenating its inputs. That
    is a fact about the step, so it is recorded as an error rather than as an
    empty output.
    """
    from app.optimizer.vendor.json_utils import extract_json

    if call.get("error"):
        return None, str(call["error"])
    text = call.get("output")
    if not text:
        return None, "the optimizer returned nothing"
    parsed = extract_json(text)
    if not isinstance(parsed, dict):
        return None, "the reply was not the JSON this stage asks for, so its result was discarded"
    return parsed, None


def _merge_level(user_prompt: str) -> int | None:
    """Which round of the hierarchical merge this was, read out of its own prompt.

    `aggregate._merge_batch` writes the level into the heading it sends, which
    makes the prompt we stored the authority on it. The `merge_level` stamped on
    the resulting edits would say the same thing, but it is added *after* the
    reply is parsed and so is absent from the reply itself.
    """
    match = re.search(r"merge level (\d+)", user_prompt or "")
    return int(match.group(1)) if match else None


def _reflect(
    *, skill_content, mode, items, recorder, edit_budget, minibatch_size,
    analyst_workers, failure_only, seed, update_mode, truncation_by_item,
    meta_skill_context="", competing_skills="",
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
            patch = run_analyst_minibatch(
                skill_content, batch,
                source_type=source_type,
                mode=mode,
                edit_budget=edit_budget,
                update_mode=update_mode,
                meta_skill_context=meta_skill_context,
                competing_skills=competing_skills,
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


def _batch_chars(batch: Sequence[dict]) -> int:
    """How much text this minibatch carries, as the analyst received it.

    The setup is counted once where the batch shares one, because that is how
    many times it was sent. Counting it per trajectory would report a prompt
    several times the size of the one the model was given, on the page whose
    job is to say how much evidence the analyst had.
    """
    trajectories = [
        item["trajectory"] for item in batch
        if isinstance(item.get("trajectory"), Trajectory)
    ]
    shared = shared_preamble(trajectories)
    total = preamble_chars(shared) if shared else 0
    for traj in trajectories:
        total += conversation_chars(traj)
        if shared is None:
            total += preamble_chars(traj)
    return total


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
