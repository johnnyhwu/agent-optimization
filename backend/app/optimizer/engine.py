"""The training loop: rollout → reflect → aggregate → clip → update → gate.

Upstream's trainer is 2,400 lines because it owns argument parsing, checkpoint
files, console rendering and eight benchmark adapters. Ours owns none of those:
the checkpoint is a database row, the console is an SSE stream, and there is one
environment. What is left is the loop itself, and the loop is the part worth
reading.

    step 0        the initial skill, measured on validation. The baseline, and
                  the only step with no candidate and no gate.
    step k ≥ 1    a minibatch of training questions answered with the *current*
                  skill → reflect on those trajectories → merge the proposed
                  patches → clip to the step's edit budget → apply → measure the
                  candidate on validation → gate.

The two rollouts in a step measure different skills, and that asymmetry is the
whole design: training is measured *before* the edit and validation *after* it.
It is why the chart offsets the training point by half a step, and why
`skill_step_no` is recorded on every rollout — a point on that chart is
meaningless without saying which skill produced it.

**Nothing here imports SQLAlchemy.** The loop talks to `OptimizationStore`, so
the behaviours that matter — a rejected candidate rolls back, a cancelled run
keeps its finished steps, a restarted backend resumes from the last completed
step, the terminal event goes out even when something throws — are testable
without a database, which is the only way an hour-long loop gets tested at all.
`tests/test_orchestrator.py` established the pattern for eval runs.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable, Sequence

from app.config import settings
from app.integrations import Seams
from app.optimizer.adapter import DEFAULT_ERROR_THRESHOLD, run_rollout, score_rollout
from app.optimizer.detector import DEFAULT_PATH_PATTERNS
from app.optimizer.gating import decide_gate
from app.optimizer.reflection import DEFAULT_REFLECT_BUDGET_CHARS, build_analyst_items
from app.optimizer.skillio import per_file_stats, total_line_changes
from app.optimizer.store import Item, OptimizationStore, ResumeState, RunSpec
from app.optimizer.update import run_update_stage
from app.optimizer.vendor.gate import select_gate_score
from app.optimizer.vendor.scheduler import build_scheduler
from app.pipeline import clip
from app.sse import hub

log = logging.getLogger(__name__)

Publisher = Callable[[dict], Awaitable[None]]


class RunAborted(RuntimeError):
    """The run cannot continue and continuing would produce a misleading result."""


class _Cancelled(Exception):
    """Internal: the stop button, raced against whatever was in flight."""


@dataclass
class _State:
    """What carries from one step to the next."""

    current_files: dict[str, str]
    current_score: float = 0.0
    current_activation: float | None = None
    best_files: dict[str, str] = field(default_factory=dict)
    best_score: float = 0.0
    best_step: int = 0
    parent_step_no: int | None = None
    # skill hash → (hard, soft, activation). Upstream's `sel_cache`: a step whose
    # edits were all skipped produces a candidate identical to the skill just
    # measured, and re-running a whole validation split to rediscover its score
    # is the most expensive no-op in the loop.
    score_cache: dict[str, tuple[float, float, float | None]] = field(default_factory=dict)


# --- Entry point ------------------------------------------------------------


async def run_optimization(
    run_id: uuid.UUID,
    *,
    store: OptimizationStore,
    seams: Seams,
    publish: Publisher | None = None,
    cancel_event: asyncio.Event | None = None,
) -> str:
    """Execute (or resume) one optimization run. Returns its terminal status.

    The `finally` is the contract: whatever happens, the run row becomes
    terminal and a `run_completed` event goes out. A run left 'running' with a
    stream nobody closes is a page that waits for the rest of its life — the
    same rule `orchestrator.run_eval` follows, and more expensive here because
    an optimization run is measured in hours.
    """
    publish = publish or _hub_publisher(run_id)
    cancel_event = cancel_event or asyncio.Event()
    state = _State(current_files={})
    status, error_message = "completed", None

    try:
        status, error_message = await _execute(
            run_id, store=store, seams=seams, publish=publish,
            cancel_event=cancel_event, state=state,
        )
    except RunAborted as exc:
        status, error_message = "failed", clip(str(exc))
    except Exception as exc:  # noqa: BLE001 - last line of defence
        log.exception("optimization run %s failed", run_id)
        status, error_message = "failed", clip(f"{type(exc).__name__}: {exc}")
    finally:
        await store.finish_run(
            run_id, status=status, error_message=error_message,
            best_step=state.best_step, best_score=state.best_score,
            completed_at=datetime.now(timezone.utc),
        )
        await publish({
            "type": "run_completed", "status": status,
            "best_step": state.best_step, "best_score": state.best_score,
            "error_message": error_message,
        })
    return status


async def _execute(
    run_id: uuid.UUID, *, store: OptimizationStore, seams: Seams,
    publish: Publisher, cancel_event: asyncio.Event, state: _State,
) -> tuple[str, str | None]:
    spec = await store.load_run(run_id)
    if spec is None:
        return "failed", "this optimization run no longer exists"
    if spec.mode not in ("isolated", "routing"):
        return "failed", f"unknown optimization mode {spec.mode!r}"

    train = await store.load_items(run_id, "train")
    val = await store.load_items(run_id, "val")
    resume = await store.load_resume_state(run_id)

    _seed_state(state, spec, resume)
    start_step = 0 if resume is None or resume.last_step_no is None else resume.last_step_no + 1

    await store.set_status(run_id, "running")
    await publish({
        "type": "snapshot", "status": "running", "step_no": start_step,
        "total_steps": spec.total_steps, "resumed": start_step > 0,
    })

    try:
        if start_step == 0:
            # Only ever on a fresh start. Re-probing on every restart would bill
            # for an agent call per restart and could abort a half-finished
            # routing run over a transient trace failure, discarding steps that
            # were already paid for.
            ok, message = await _preflight(
                run_id, spec=spec, items=val or train, store=store, seams=seams,
                publish=publish, cancel_event=cancel_event,
            )
            if not ok and spec.mode == "routing":
                return "failed", message
            await _baseline_step(
                run_id, spec=spec, val=val, store=store, seams=seams,
                publish=publish, cancel_event=cancel_event, state=state,
            )
            start_step = 1

        scheduler = _build_scheduler(spec)
        for step_no in range(start_step, spec.total_steps + 1):
            await _check_cancel(run_id, store, cancel_event)
            await _step(
                run_id, step_no=step_no, spec=spec, train=train, val=val,
                store=store, seams=seams, publish=publish, cancel_event=cancel_event,
                state=state, edit_budget=scheduler.get_lr(step_no),
            )
    except _Cancelled:
        return "cancelled", None
    except RunAborted:
        raise

    return "completed", None


def _seed_state(state: _State, spec: RunSpec, resume: ResumeState | None) -> None:
    if resume is not None and resume.last_step_no is not None:
        state.current_files = dict(resume.current_files)
        state.current_score = resume.current_score
        state.best_files = dict(resume.best_files)
        state.best_score = resume.best_score
        state.best_step = resume.best_step
        state.parent_step_no = resume.parent_step_no
        state.score_cache = dict(resume.score_cache)
        return
    state.current_files = dict(spec.initial_skill)
    state.best_files = dict(spec.initial_skill)


# --- Pre-flight -------------------------------------------------------------


async def probe_activation(
    items: Sequence[Item], *, spec: RunSpec, seams: Seams, cancel_event: asyncio.Event,
) -> list:
    """One question, answered with the initial skill, to see what can be observed.

    Cheap on purpose — a single agent call — because its whole value is being
    the thing that happens before the expensive part.
    """
    if not items:
        return []
    return await run_rollout(
        [items[0]],
        skill_files=spec.initial_skill,
        mode=spec.mode,
        skill_name=spec.skill_name,
        seams=seams,
        config=spec.config,
        workspace_baseline=spec.workspace_baseline,
        cancel_event=cancel_event,
        concurrency=1,
        detectable=bool(spec.detector.get("detectable")),
        path_patterns=spec.detector.get("path_patterns") or DEFAULT_PATH_PATTERNS,
    )


async def _preflight(
    run_id, *, spec: RunSpec, items, store: OptimizationStore, seams: Seams,
    publish: Publisher, cancel_event: asyncio.Event,
) -> tuple[bool, str]:
    """Can this run observe whether the agent used the skill at all?

    The two modes need very different answers. Routing's gate *compares
    activation rates*, so a routing run that cannot see activation has no way to
    measure its own outcome — it would spend an hour and produce a number that
    means nothing. Isolated's gate is accuracy, which is measurable either way,
    so a silent detector there costs it one column in the UI and nothing else.
    Aborting isolated runs on this would lock out every agent whose traces do
    not happen to name skill file paths.
    """
    rows = await probe_activation(items, spec=spec, seams=seams, cancel_event=cancel_event)
    observed = [row for row in rows if getattr(row, "activated", None) is not None]
    ok = any(row.activated for row in observed)
    hit = rows[0].detector_hit if rows else "none"
    skills_read = (rows[0].skills_read or []) if rows else []

    if ok:
        message = f"the agent read the skill ({hit})"
    elif spec.mode == "routing":
        message = (
            "no activation could be detected on the probe question. A routing run "
            "is gated on activation, so it cannot measure whether a description "
            "edit helped — fix the detector settings, or use isolated mode."
        )
    else:
        message = (
            "no activation could be detected on the probe question. Accuracy is "
            "still measured normally; the activation column will read 'unknown'."
        )

    await store.finish_run(run_id, detector={
        **spec.detector,
        "preflight": {"ok": ok, "detector_hit": hit, "skills_read": skills_read,
                      "message": message},
    })
    await publish({
        "type": "preflight", "ok": ok, "detector_hit": hit,
        "skills_read": skills_read, "message": message,
    })
    return ok, message


# --- Steps ------------------------------------------------------------------


async def _baseline_step(
    run_id, *, spec: RunSpec, val, store: OptimizationStore, seams: Seams,
    publish: Publisher, cancel_event: asyncio.Event, state: _State,
) -> None:
    """Step 0: the initial skill on held-out data, and nothing else.

    No training rollout, because there is no candidate to compare against yet —
    a batch of agent calls bought for a point the chart does not plot.
    """
    step_id = await store.start_step(
        run_id, step_no=0, epoch_no=0, step_in_epoch=0, parent_step_no=None
    )
    await publish({"type": "step_started", "step_no": 0, "epoch_no": 0, "phase": "baseline"})

    summary, retried = await _rollout(
        val, spec=spec, skill_files=spec.initial_skill, split="val", skill_step_no=0,
        store=store, seams=seams, publish=publish, cancel_event=cancel_event, step_no=0,
    )
    await store.record_rollout(step_id, summary)
    await _publish_rollout(publish, 0, summary)

    content_hash = skill_hash(spec.initial_skill)
    await store.record_skill(
        run_id, step_no=0, kind="initial", files=dict(spec.initial_skill),
        content_hash=content_hash, per_file_stats={},
    )

    score = _score_of(summary, spec)
    state.current_score = state.best_score = score
    state.best_step = 0
    state.current_activation = summary.activation_rate
    state.score_cache[content_hash] = (
        summary.hard or 0.0, summary.soft or 0.0, summary.activation_rate
    )

    await store.finish_step(
        step_id, status="done", retried=retried, candidate_hash=content_hash,
        current_score=score, best_score=score,
        skill_len=sum(len(text) for text in spec.initial_skill.values()),
        completed_at=datetime.now(timezone.utc),
    )


async def _step(
    run_id, *, step_no: int, spec: RunSpec, train, val, store: OptimizationStore,
    seams: Seams, publish: Publisher, cancel_event: asyncio.Event, state: _State,
    edit_budget: int,
) -> None:
    """One turn of the loop, and a step row that is never left mid-flight.

    A step interrupted between its two rollouts is genuinely incomplete — there
    is a candidate but no score for it — so it is closed as `aborted` rather
    than left `running` (a row the UI would show as live on a finished run) or
    marked `done` (a lie the resume path would then trust and skip). `aborted`
    is exactly what a resumed run needs to see in order to redo the step.
    """
    step_id = await store.start_step(
        run_id, step_no=step_no,
        epoch_no=(step_no - 1) // max(spec.steps_per_epoch, 1) + 1,
        step_in_epoch=(step_no - 1) % max(spec.steps_per_epoch, 1) + 1,
        parent_step_no=state.parent_step_no,
    )
    try:
        await _run_step(
            run_id, step_id=step_id, step_no=step_no, spec=spec, train=train, val=val,
            store=store, seams=seams, publish=publish, cancel_event=cancel_event,
            state=state, edit_budget=edit_budget,
        )
    except _Cancelled:
        await store.finish_step(
            step_id, status="aborted", abort_reason="cancelled",
            completed_at=datetime.now(timezone.utc),
        )
        raise
    except RunAborted as exc:
        await store.finish_step(
            step_id, status="aborted", abort_reason=clip(str(exc)),
            completed_at=datetime.now(timezone.utc),
        )
        raise


async def _run_step(
    run_id, *, step_id, step_no: int, spec: RunSpec, train, val,
    store: OptimizationStore, seams: Seams, publish: Publisher,
    cancel_event: asyncio.Event, state: _State, edit_budget: int,
) -> None:
    config = spec.config
    steps_per_epoch = max(spec.steps_per_epoch, 1)
    epoch_no = (step_no - 1) // steps_per_epoch + 1
    step_in_epoch = (step_no - 1) % steps_per_epoch + 1

    await publish({
        "type": "step_started", "step_no": step_no, "epoch_no": epoch_no,
        "step_in_epoch": step_in_epoch, "phase": "rollout",
    })

    # --- 1. training rollout, against the skill currently in force ----------
    batch = train_batch(
        train, epoch_no=epoch_no, step_in_epoch=step_in_epoch,
        batch_size=spec.batch_size, seed=config.get("seed"),
    )
    train_summary, retried = await _rollout(
        batch, spec=spec, skill_files=state.current_files, split="train",
        skill_step_no=state.parent_step_no or 0, store=store, seams=seams,
        publish=publish, cancel_event=cancel_event, step_no=step_no,
    )
    await store.record_rollout(step_id, train_summary)
    await _publish_rollout(publish, step_no, train_summary)
    await _check_cancel(run_id, store, cancel_event)

    # --- 2. reflect, aggregate, clip, apply ---------------------------------
    items, ledger = build_analyst_items(
        train_summary.results,
        questions={item.item_key: item.question for item in batch},
        ground_truths={item.item_key: item.ground_truth_response for item in batch},
        budget_chars=int(config.get("reflect_budget_chars") or DEFAULT_REFLECT_BUDGET_CHARS),
    )
    outcome = await asyncio.to_thread(
        run_update_stage,
        files=state.current_files,
        skill_dir=spec.skill_name,
        mode=spec.mode,
        items=items,
        client=seams.optimizer,
        edit_budget=edit_budget,
        minibatch_size=int(config.get("minibatch_size") or 8),
        analyst_workers=int(config.get("analyst_workers") or 4),
        merge_batch_size=int(config.get("merge_batch_size") or 8),
        failure_only=bool(config.get("failure_only")),
        seed=config.get("seed"),
        truncation_by_item=ledger,
    )
    for record in outcome.minibatches:
        await store.record_minibatch(
            step_id,
            minibatch_no=record.minibatch_no, source_type=record.source_type,
            # Not a column: the store uses it to stamp `minibatch_no` onto the
            # training results this analyst was shown, which is the only record
            # of which failures were grouped together.
            item_keys=record.item_keys,
            n_items=record.n_items, prompt_system=record.prompt_system,
            prompt_user=record.prompt_user, raw_output=record.raw_output,
            truncation=record.truncation, chars_before=record.chars_before,
            chars_after=record.chars_after, error=record.error,
            duration_ms=record.duration_ms,
        )
    await publish({
        "type": "reflect_done", "step_no": step_no,
        "n_minibatches": len(outcome.minibatches),
        "n_patches": outcome.n_edits_merged,
    })

    candidate = outcome.files
    candidate_hash = skill_hash(candidate)
    stats = per_file_stats(state.current_files, candidate)
    lines_added, lines_removed = total_line_changes(state.current_files, candidate)
    await store.record_skill(
        run_id, step_no=step_no, kind="candidate", files=candidate,
        content_hash=candidate_hash, per_file_stats=stats,
    )
    await publish({
        "type": "update_done", "step_no": step_no,
        "n_edits_applied": outcome.n_edits_applied,
        "lines_added": lines_added, "lines_removed": lines_removed,
    })
    await _check_cancel(run_id, store, cancel_event)

    # --- 3. validation rollout, against the candidate -----------------------
    cached = state.score_cache.get(candidate_hash)
    if cached is not None:
        hard, soft, activation = cached
        from_cache = True
    else:
        val_summary, val_retried = await _rollout(
            val, spec=spec, skill_files=candidate, split="val", skill_step_no=step_no,
            store=store, seams=seams, publish=publish, cancel_event=cancel_event,
            step_no=step_no,
        )
        retried = retried or val_retried
        await store.record_rollout(step_id, val_summary)
        await _publish_rollout(publish, step_no, val_summary)
        hard = val_summary.hard or 0.0
        soft = val_summary.soft or 0.0
        activation = val_summary.activation_rate
        state.score_cache[candidate_hash] = (hard, soft, activation)
        from_cache = False

    # --- 4. the gate --------------------------------------------------------
    gate = decide_gate(
        mode=spec.mode, step_no=step_no,
        cand_hard=hard, cand_soft=soft, cand_activation=activation,
        current_score=state.current_score, current_activation=state.current_activation,
        best_score=state.best_score, best_step=state.best_step,
        metric=config.get("gate_metric") or "hard",
        mixed_weight=float(config.get("mixed_weight") or 0.5),
    )

    if gate.accepted:
        state.current_files = candidate
        state.current_activation = activation
        state.parent_step_no = step_no
    if gate.action == "accept_new_best":
        state.best_files = candidate
    state.current_score = gate.current_score
    state.best_score = gate.best_score
    state.best_step = gate.best_step

    await store.finish_step(
        step_id, status="done", retried=retried, edit_budget=edit_budget,
        gate_action=gate.action, gate_reject_reason=gate.reject_reason,
        candidate_hash=candidate_hash, candidate_from_cache=from_cache,
        n_edits_merged=outcome.n_edits_merged, n_edits_ranked=outcome.n_edits_ranked,
        n_edits_applied=outcome.n_edits_applied, n_edits_skipped=outcome.n_edits_skipped,
        edit_reports=outcome.reports,
        lines_added=lines_added, lines_removed=lines_removed, files_touched=len(stats),
        skill_len=sum(len(text) for text in candidate.values()),
        edit_summary=outcome.edit_summary,
        current_score=gate.current_score, best_score=gate.best_score,
        tokens=outcome.tokens, completed_at=datetime.now(timezone.utc),
    )
    await publish({
        "type": "gate_done", "step_no": step_no, "action": gate.action,
        "reject_reason": gate.reject_reason, "candidate_score": gate.candidate_score,
        "current_score": gate.current_score, "best_score": gate.best_score,
        "from_cache": from_cache,
    })


# --- Rollouts ---------------------------------------------------------------


async def _rollout(
    items, *, spec: RunSpec, skill_files, split: str, skill_step_no: int,
    store: OptimizationStore, seams: Seams, publish: Publisher,
    cancel_event: asyncio.Event, step_no: int,
):
    """One split, measured once, with a single retry if too much of it failed.

    `score_rollout` refuses to score a batch that mostly failed, and refusing is
    right: a step measured on 60% of its questions is not a smaller measurement
    but an unrepresentative one, and the gate cannot tell the difference.

    One retry, because the common cause is a thirty-second outage and losing an
    entire run to that would be absurd. Not two, because twice in a row is not a
    blip — and the alternative to stopping is writing skill edits derived from a
    batch that did not run, which the developer would discover from a chart that
    looks completely ordinary.
    """
    config = spec.config
    threshold = float(config.get("error_threshold") or DEFAULT_ERROR_THRESHOLD)
    retried = False

    for attempt in (1, 2):
        rows = await run_rollout(
            items,
            skill_files=skill_files,
            mode=spec.mode,
            skill_name=spec.skill_name,
            seams=seams,
            config=config,
            workspace_baseline=spec.workspace_baseline,
            cancel_event=cancel_event,
            concurrency=int(config.get("concurrency") or settings.run_concurrency),
            detectable=bool(spec.detector.get("detectable")),
            path_patterns=spec.detector.get("path_patterns") or DEFAULT_PATH_PATTERNS,
        )
        summary = score_rollout(
            rows, split=split, skill_step_no=skill_step_no, error_threshold=threshold
        )
        if not summary.aborted:
            return summary, retried

        # A batch that "failed" because the stop button was pressed is not a
        # flaky agent, and retrying it would spend a second batch discovering
        # that the run is still cancelled.
        await _check_cancel(spec.id, store, cancel_event)

        if attempt == 1:
            retried = True
            log.warning(
                "optimization %s step %s %s rollout aborted (%s); retrying once",
                spec.id, step_no, split, summary.abort_reason,
            )
            await publish({
                "type": "rollout_retry", "step_no": step_no, "split": split,
                "reason": summary.abort_reason,
            })

    raise RunAborted(
        f"step {step_no}: the {split} rollout failed twice — {summary.abort_reason}"
    )


async def _publish_rollout(publish: Publisher, step_no: int, summary) -> None:
    await publish({
        "type": "rollout_done", "step_no": step_no, "split": summary.split,
        "hard": summary.hard, "soft": summary.soft,
        "activation_rate": summary.activation_rate,
        "n_items": summary.n_items, "n_scored": summary.n_scored,
        "n_agent_error": summary.n_agent_error, "n_judge_error": summary.n_judge_error,
        "latency_min_ms": summary.latency_min_ms,
        "latency_p50_ms": summary.latency_p50_ms,
        "latency_max_ms": summary.latency_max_ms,
    })


# --- Odds and ends ----------------------------------------------------------


def train_batch(
    items: Sequence[Item], *, epoch_no: int, step_in_epoch: int,
    batch_size: int, seed: int | None,
) -> list[Item]:
    """This step's slice of the training split.

    Reshuffled per epoch, so the minibatches a question is reflected on beside
    change between epochs — patterns that only show up across groups never
    surface if the groups are fixed for the whole run.

    Seeded, because the composition is never stored: a run resumed mid-epoch
    re-derives its batch, and an unseeded shuffle would quietly train on a
    different sample than the interrupted run had planned.
    """
    ordered = list(items)
    random.Random((seed or 0) + epoch_no).shuffle(ordered)
    start = (step_in_epoch - 1) * max(batch_size, 1)
    return ordered[start:start + max(batch_size, 1)]


def skill_hash(files) -> str:
    """A stable identity for one skill directory, for the validation cache."""
    payload = json.dumps(dict(files), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _score_of(summary, spec: RunSpec) -> float:
    return select_gate_score(
        summary.hard or 0.0, summary.soft or 0.0,
        spec.config.get("gate_metric") or "hard",
        float(spec.config.get("mixed_weight") or 0.5),
    )


def _build_scheduler(spec: RunSpec):
    config = spec.config
    return build_scheduler(
        mode=config.get("scheduler") or "constant",
        max_lr=int(config.get("learning_rate") or 8),
        min_lr=int(config.get("min_learning_rate") or 2),
        total_steps=max(spec.total_steps, 1),
    )


async def _check_cancel(run_id, store: OptimizationStore, cancel_event: asyncio.Event) -> None:
    """Both halves of cancellation: the in-process event and the durable flag.

    The event is immediate but dies with the process. The flag is what the UI
    wrote and what survives a restart — so a run resumed after a cancel that
    was requested while the backend was down must still stop, and consulting
    only the event would have it carry on spending money.
    """
    if cancel_event.is_set() or await store.cancel_requested(run_id):
        raise _Cancelled()


def _hub_publisher(run_id: uuid.UUID) -> Publisher:
    async def publish(event: dict) -> None:
        await hub.publish(run_id, event)

    return publish
