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
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Awaitable, Callable, Sequence

from app.config import settings
from app.integrations import Seams
from app.optimizer.adapter import make_probe_marker, run_rollout, score_rollout
from app.optimizer.detector import DEFAULT_PATH_PATTERNS
from app.optimizer.gating import decide_gate
from app.optimizer.hyperparams import resolve_algorithm
from app.optimizer.reflection import build_analyst_items
from app.optimizer.skillio import find_answer_leaks, per_file_stats, total_line_changes
from app.optimizer.stopping import (
    STOP_FINISHED,
    StopCounters,
    StopPolicy,
    decide_stop,
)
from app.optimizer.store import Item, OptimizationStore, ResumeState, RunSpec
from app.optimizer.longitudinal import run_epoch_boundary
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
    # epoch_no → the step whose validation rollout measured the skill in force at
    # the end of that epoch. The slow update compares two of these. Epoch 0 is the
    # baseline; an epoch that accepted nothing inherits the previous mark, which
    # is how "the skill did not change" is detected without comparing hashes.
    epoch_mark: dict[int, int] = field(default_factory=lambda: {0: 0})
    # Upstream's two longitudinal memories, carried across epoch boundaries.
    slow_update_text: str = ""
    meta_skill_text: str = ""
    # The skill as each epoch ended, so a boundary can show the optimizer what
    # the previous epoch's version actually said.
    epoch_files: dict[int, dict[str, str]] = field(default_factory=dict)
    # How many rollouts in a row this run has had to refuse, per split. Carried
    # here rather than kept local to the loop because a resumed run rebuilds
    # them from its step rows — a counter that reset on every restart is one a
    # crash-looping agent server could never trip.
    counters: StopCounters = field(default_factory=StopCounters)


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
    status, error_message, stop_reason = "completed", None, STOP_FINISHED

    try:
        status, error_message, stop_reason = await _execute(
            run_id, store=store, seams=seams, publish=publish,
            cancel_event=cancel_event, state=state,
        )
    except RunAborted as exc:
        status, error_message, stop_reason = "failed", clip(str(exc)), "failed"
    except Exception as exc:  # noqa: BLE001 - last line of defence
        log.exception("optimization run %s failed", run_id)
        status, error_message = "failed", clip(f"{type(exc).__name__}: {exc}")
        stop_reason = "failed"
    finally:
        await store.finish_run(
            run_id, status=status, error_message=error_message,
            # Why it ended, which `status` cannot say: a run that stopped
            # because validation hit its target and one that ran out of steps
            # are both 'completed', and the difference is the whole result.
            stop_reason=stop_reason,
            best_step=state.best_step, best_score=state.best_score,
            completed_at=datetime.now(timezone.utc),
        )
        await publish({
            "type": "run_completed", "status": status,
            "stop_reason": stop_reason,
            "best_step": state.best_step, "best_score": state.best_score,
            "error_message": error_message,
        })
    return status


async def _execute(
    run_id: uuid.UUID, *, store: OptimizationStore, seams: Seams,
    publish: Publisher, cancel_event: asyncio.Event, state: _State,
) -> tuple[str, str | None, str]:
    """The loop itself. Returns `(status, error_message, stop_reason)`."""
    spec = await store.load_run(run_id)
    if spec is None:
        return "failed", "this optimization run no longer exists", "failed"
    if spec.mode not in ("isolated", "routing"):
        return "failed", f"unknown optimization mode {spec.mode!r}", "failed"

    train = await store.load_items(run_id, "train")
    val = await store.load_items(run_id, "val")
    resume = await store.load_resume_state(run_id)

    _seed_state(state, spec, resume)
    start_step = 0 if resume is None or resume.last_step_no is None else resume.last_step_no + 1
    # Resolved once, from what this run was started with. Every rule about when
    # to stop early lives in `stopping.py`; the loop only asks.
    policy = StopPolicy.from_config(spec.config)

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
            preflight = await _preflight(
                run_id, spec=spec, items=val or train, store=store, seams=seams,
                publish=publish, cancel_event=cancel_event,
            )
            # An override that never arrives makes both modes unmeasurable, so
            # this one stops every run; a silent detector only stops routing.
            if preflight.override_ignored:
                return "failed", preflight.message, "failed"
            if not preflight.detector_ok and spec.mode == "routing":
                return "failed", preflight.message, "failed"
            await _baseline_step(
                run_id, spec=spec, val=val, store=store, seams=seams,
                publish=publish, cancel_event=cancel_event, state=state,
                policy=policy,
            )
            start_step = 1

        scheduler = _build_scheduler(spec)
        steps_per_epoch = max(spec.steps_per_epoch, 1)
        for step_no in range(start_step, spec.total_steps + 1):
            await _check_cancel(run_id, store, cancel_event)
            val_score = await _step(
                run_id, step_no=step_no, spec=spec, train=train, val=val,
                store=store, seams=seams, publish=publish, cancel_event=cancel_event,
                state=state, edit_budget=scheduler.get_lr(step_no), policy=policy,
            )
            # Before the epoch boundary, not after: the boundary is a call on
            # the largest model configured, and a run that has already decided
            # to stop should not buy one.
            reason = decide_stop(
                policy, state.counters, step_no=step_no,
                best_step=state.best_step, last_val_score=val_score,
            )
            if reason is not None:
                # No event of its own: `run_completed` carries `stop_reason` and
                # follows within the second, and a second event saying the same
                # thing is a wire message every consumer has to learn to ignore.
                log.info("optimization %s stopping early: %s", run_id, reason)
                return "completed", None, reason
            # The epoch boundary, and the only place anything looks across steps.
            if step_no % steps_per_epoch == 0:
                await _epoch_boundary(
                    run_id, epoch_no=(step_no - 1) // steps_per_epoch + 1,
                    step_no=step_no, spec=spec, val=val, store=store, seams=seams,
                    publish=publish, state=state,
                )
    except _Cancelled:
        return "cancelled", None, "cancelled"
    except RunAborted:
        raise

    return "completed", None, STOP_FINISHED


def _seed_state(state: _State, spec: RunSpec, resume: ResumeState | None) -> None:
    if resume is not None and resume.last_step_no is not None:
        state.current_files = dict(resume.current_files)
        state.current_score = resume.current_score
        state.best_files = dict(resume.best_files)
        state.best_score = resume.best_score
        state.best_step = resume.best_step
        state.parent_step_no = resume.parent_step_no
        state.score_cache = dict(resume.score_cache)
        state.counters = StopCounters(
            train_errors=resume.train_error_streak,
            val_errors=resume.val_error_streak,
        )
        return
    state.current_files = dict(spec.initial_skill)
    state.best_files = dict(spec.initial_skill)
    state.epoch_files[0] = dict(spec.initial_skill)


# --- Pre-flight -------------------------------------------------------------


def probe_question(question: str, skill_name: str) -> str:
    """The pre-flight question, with a nudge to read the skill first.

    Without it the probe only learns what the agent *chose* to do on one
    question, which conflates two things worth separating: whether a detector
    can see a skill being read at all, and whether this particular question
    happened to call for one. Asking for the read isolates the first.

    It is also what makes the marker check work against a tool-using agent. Such
    an agent's trace carries paths, not file bodies — until it actually reads a
    file, at which point the contents come back as a tool result and land in the
    trace either as that observation's output or in the next generation's
    messages. Either way the marker becomes visible.

    The **directory** name, never a file path: a path in the prompt is a path
    the agent can echo into a tool call, and the tool-path detector would then
    be matching our own instruction rather than the agent's behaviour.
    """
    return f"{question}\n\n(you must first read the {skill_name} skill)"


async def probe_activation(
    items: Sequence[Item], *, spec: RunSpec, seams: Seams, cancel_event: asyncio.Event,
) -> list:
    """One question, answered with a marked copy of the initial skill.

    Cheap on purpose — a single question — because its whole value is being the
    thing that happens before the expensive part. It answers two questions at
    once: can we see this agent load a skill, and did it use the files we sent
    rather than its own?

    The skill sent is `initial_skill`, i.e. what the agent already has, so the
    probe measures the machinery and not a candidate. The marker is the only
    difference between the two copies, and it is what makes the second question
    answerable at all.
    """
    if not items:
        return []
    item = items[0]
    return await run_rollout(
        [replace(item, question=probe_question(item.question, spec.skill_name))],
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
        probe_marker=make_probe_marker(),
    )


@dataclass
class PreflightResult:
    """What the probe established, and whether the run may go on.

    Two independent findings, kept apart because they have different
    consequences. `detector_ok` is about what we can *see* and only routing
    depends on it. `override_ignored` is about whether the experiment is
    possible at all, and stops both modes.
    """

    detector_ok: bool
    override_ignored: bool
    message: str


async def _preflight(
    run_id, *, spec: RunSpec, items, store: OptimizationStore, seams: Seams,
    publish: Publisher, cancel_event: asyncio.Event,
) -> PreflightResult:
    """Can this run observe whether the agent used the skill at all — and did the
    agent use *our* copy of it?

    The two modes need very different answers. Routing's gate *compares
    activation rates*, so a routing run that cannot see activation has no way to
    measure its own outcome — it would spend an hour and produce a number that
    means nothing. Isolated's gate is accuracy, which is measurable either way,
    so a silent detector there costs it one column in the UI and nothing else.
    Aborting isolated runs on this would lock out every agent whose traces do
    not happen to name skill file paths.

    **A successful probe turns the detector's absence into evidence.** That is
    what this function is for, and until now it was the one thing it did not do.
    `detect_activation` will only answer `False` when the caller has said a
    detector demonstrably works against this agent — otherwise "nothing was seen"
    is `None`, unknown, and `score_rollout` leaves unknowns out of the fraction
    rather than averaging them in as zeros. Both halves of that are right. What
    was missing is the connection: the probe proved the detector fires, wrote
    `preflight.ok` for the UI, and never set `detectable`, which is the flag the
    detector actually reads. It therefore stayed `False` on every run ever
    executed, and a question where the agent called no tool at all scored
    `None` and dropped out of the denominator. Nine questions reading the skill
    and one reading nothing reported 100%, which is exactly the number that
    cannot be trusted — a rate is worthless if the questions it is quietly
    excluding are the ones it exists to catch.

    **The second finding is whether the override was applied at all.** The probe
    ships a marker that exists only in the copy we sent (`adapter`), so its
    absence from a trace that demonstrably *did* read the skill means the agent
    answered from its own files. That invalidates both modes — every step would
    measure the same deployed text — so it stops the run here rather than after
    an hour of rollouts that could not have shown anything.

    The marker is only ever read as evidence when something was observed. An
    agent whose trace shows no skill read at all is one we cannot see into, and
    reporting that as a violation would lock out working agents.
    """
    rows = await probe_activation(items, spec=spec, seams=seams, cancel_event=cancel_event)
    observed = [row for row in rows if getattr(row, "activated", None) is not None]
    ok = any(row.activated for row in observed)
    hit = rows[0].detector_hit if rows else "none"
    skills_read = (rows[0].skills_read or []) if rows else []

    # Tri-state, and `None` must never harden into a negative: it is the default
    # on every row, so anything that did not actually run the marker check —
    # a trace that never landed, a stubbed probe, an agent whose trace shows no
    # file content to look in — reads as "not asked". `verify_probe_marker`
    # already applied that last condition, so a `False` here means the trace
    # carried the skill's text and our marker was not in it.
    verified = rows[0].override_verified if rows else None
    # `hit == "none"` cannot coexist with a legitimate `False` — the detector
    # reports `content` whenever body text was seen, which is the same condition
    # `verify_probe_marker` requires before it will answer `False` at all. Kept
    # anyway: this branch hard-fails a run somebody is paying for, and two
    # independent conditions agreeing is cheap insurance against a later change
    # to either one.
    override_ignored = verified is False and hit != "none"

    # In memory for the steps this process is about to run, and persisted below
    # for the ones a restart will run — the probe is bought once, on a fresh
    # start only, so a resumed run has to read this decision back rather than
    # re-establish it.
    detector = {**spec.detector}
    if ok:
        detector["detectable"] = True
        spec.detector["detectable"] = True

    if override_ignored:
        message = (
            f"the agent read the skill ({hit}) but answered from its own copy: the "
            "marker this run sent was not in the trace, though SKILL.md's own text "
            "was. The agent server is most likely not applying metadata.skills, so "
            "every step would measure the deployed skill instead of the candidate — "
            "the run is stopped rather than spending an hour to produce a flat "
            "line. The one innocent explanation is an agent that strips HTML "
            "comments while building its prompt; the marker is a comment, and "
            "nothing else distinguishes the two copies."
        )
    elif ok and verified is True:
        message = f"the agent read the skill we sent ({hit})"
    elif ok:
        # `ok` is the activation detector's verdict alone. Saying "we sent" on
        # the strength of it would assert the very thing the marker check
        # declined to conclude — and this sentence is what somebody would quote
        # back after discovering a run had measured the deployed skill all along.
        message = (
            f"the agent read the skill ({hit}), but the override could not be "
            "verified: the trace does not show SKILL.md's own text, so there was "
            "nowhere for this run's marker to appear. Accuracy is measured "
            "normally; nothing here proves the candidate reached the agent."
        )
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
        **detector,
        "preflight": {"ok": ok, "detector_hit": hit, "skills_read": skills_read,
                      "override_verified": verified, "message": message},
    })
    await publish({
        "type": "preflight", "ok": ok, "detector_hit": hit,
        "skills_read": skills_read, "override_verified": verified,
        "message": message,
    })
    return PreflightResult(
        detector_ok=ok, override_ignored=override_ignored, message=message
    )


# --- Steps ------------------------------------------------------------------


async def _baseline_step(
    run_id, *, spec: RunSpec, val, store: OptimizationStore, seams: Seams,
    publish: Publisher, cancel_event: asyncio.Event, state: _State,
    policy: StopPolicy,
) -> None:
    """Step 0: the initial skill on held-out data, and nothing else.

    No training rollout, because there is no candidate to compare against yet —
    a batch of agent calls bought for a point the chart does not plot.

    This is the one rollout whose failure still ends the run. Every later step
    is refused and the loop carries on, because there is a candidate to throw
    away and a next step to try — here there is neither, and every number the
    run would go on to produce is a comparison against this one.
    """
    step_id = await store.start_step(
        run_id, step_no=0, epoch_no=0, step_in_epoch=0, parent_step_no=None
    )
    await publish({"type": "step_started", "step_no": 0, "epoch_no": 0, "phase": "baseline"})

    summary = await _rollout(
        val, spec=spec, skill_files=spec.initial_skill, split="val", skill_step_no=0,
        store=store, seams=seams, publish=publish, cancel_event=cancel_event, step_no=0,
        error_share=policy.error_share("val"),
    )
    await store.record_rollout(step_id, summary)
    await _publish_rollout(publish, 0, summary)
    if summary.aborted:
        raise RunAborted(
            f"the baseline could not be measured — {summary.abort_reason}. "
            "Every later step is a comparison against this number, so a run "
            "started from it would report improvements nobody measured."
        )

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
        step_id, status="done", candidate_hash=content_hash,
        # The baseline is a measurement too, and every later step is compared
        # against it — so a deploy between creating the run and running step 0
        # invalidates the whole chart rather than one point of it.
        workspace_version=await _agent_version(seams),
        current_score=score, best_score=score,
        skill_len=sum(len(text) for text in spec.initial_skill.values()),
        completed_at=datetime.now(timezone.utc),
    )


async def _step(
    run_id, *, step_no: int, spec: RunSpec, train, val, store: OptimizationStore,
    seams: Seams, publish: Publisher, cancel_event: asyncio.Event, state: _State,
    edit_budget: int, policy: StopPolicy,
) -> float | None:
    """One turn of the loop, and a step row that is never left mid-flight.

    Returns the candidate's validation score, or None when this step produced
    no trustworthy one — a skipped step, or one whose validation split was
    refused. That distinction is the caller's: a target of 90% must not be
    reached by a step that measured nothing.

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
        return await _run_step(
            run_id, step_id=step_id, step_no=step_no, spec=spec, train=train, val=val,
            store=store, seams=seams, publish=publish, cancel_event=cancel_event,
            state=state, edit_budget=edit_budget, policy=policy,
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


async def _epoch_boundary(
    run_id, *, epoch_no: int, step_no: int, spec: RunSpec, val, store: OptimizationStore,
    seams: Seams, publish: Publisher, state: _State,
) -> None:
    """Upstream's slow update, at the end of an epoch. Off unless asked for.

    The comparison is the validation split under the skill that ended the
    previous epoch versus the one that ends this one — see `longitudinal.py` for
    why that split and not the training batch. Both marks are step numbers, and
    `parent_step_no` already tracks the last accepted step, so "which skill was
    in force" needs nothing new to be recorded.

    An epoch that accepted no candidate ends on the skill it began with. There
    is nothing longitudinal about comparing a skill with itself, and asking the
    largest model configured to write guidance about a change that did not
    happen is worse than not asking.
    """
    previous = state.epoch_mark.get(epoch_no - 1, 0)
    current = state.parent_step_no if state.parent_step_no is not None else 0
    state.epoch_mark[epoch_no] = current

    params = resolve_algorithm(spec.config)
    wants = params["slow_update"] or params["meta_skill"]
    if not wants or current == previous or seams.optimizer is None:
        return

    outcome = await asyncio.to_thread(
        run_epoch_boundary,
        files=state.current_files,
        prev_files=state.epoch_files.get(epoch_no - 1, {}),
        skill_dir=spec.skill_name,
        items=[{"id": item.item_key, "question": item.question} for item in val],
        results_prev=await store.load_val_results(run_id, previous),
        results_curr=await store.load_val_results(run_id, current),
        client=seams.optimizer,
        prev_slow_update_text=state.slow_update_text,
        prev_meta_skill_text=state.meta_skill_text,
        slow_update=params["slow_update"],
        meta_skill=params["meta_skill"],
    )

    state.meta_skill_text = outcome.meta_skill_text or state.meta_skill_text
    state.epoch_files[epoch_no] = dict(outcome.files)
    if not outcome.changed:
        return

    # The skill changed outside a step, so it needs a snapshot of its own or the
    # next step's diff would show the guidance block as that step's own edit —
    # attributing one model's writing to another, on the page whose job is to
    # say who changed what. `skill_diff` resolves a base to this kind first.
    state.current_files = dict(outcome.files)
    state.slow_update_text = outcome.slow_update_text
    await store.record_skill(
        run_id, step_no=current, kind="slow_update", files=dict(outcome.files),
        content_hash=skill_hash(outcome.files),
        per_file_stats=per_file_stats(state.epoch_files.get(epoch_no - 1, {}), outcome.files),
    )
    await publish({
        "type": "slow_update_done", "step_no": step_no, "epoch_no": epoch_no,
        "improved": outcome.n_improved, "regressed": outcome.n_regressed,
        "persistent_fail": outcome.n_persistent_fail,
    })


async def _agent_version(seams: Seams) -> str | None:
    """The agent's config version right now, or None if it cannot be had.

    A run is a comparison and it only holds if the other side holds still. A
    config deployed to the agent server halfway through makes the steps before
    and after it measurements of two different systems, and the gate will accept
    or reject a candidate for a reason that has nothing to do with its edits —
    while the only visible symptom is the accuracy moving, which is exactly what
    the chart is supposed to be showing.

    Recorded per step rather than as one flag on the run, because "which steps
    are comparable" is the question a reader has once they know something moved.

    Never raises. An hour of agent calls is already paid for by the time this
    runs, and discarding it over a flaky read of `GET /skills` would be
    throwing away the measurement to report a caveat about it. `None` rather
    than `""` for the same reason the detector distinguishes "unknown" from
    "no": an empty string would disagree with every pinned version and warn
    about drift on every run that never probed.
    """
    client = getattr(seams, "workspace", None)
    if client is None:
        return None
    try:
        return await client.get_version()
    except Exception as exc:  # noqa: BLE001 - an observation, not a dependency
        log.warning("could not read the agent's config version: %s", exc)
        return None


async def _finish_unscored_step(
    store: OptimizationStore, step_id, *, seams: Seams, state: _State,
    action: str, reason: str, **fields,
) -> None:
    """Close a step that produced no trustworthy validation score.

    Two ways to get here, and they are told apart by `action`: `skip` means the
    training batch never came back so no candidate exists, `reject` means one
    exists and was dropped unjudged. Neither changes the skill in force, and
    neither is a gate decision — the gate was never asked.

    `current_score` and `best_score` are unchanged by definition and written
    anyway: a resumed run rebuilds its working state by replaying these two
    columns (`store.load_resume_state`), and a null on a finished step would
    read as "this step lost the run's score".
    """
    await store.finish_step(
        step_id, status="done", gate_action=action, gate_reject_reason=reason,
        workspace_version=await _agent_version(seams),
        current_score=state.current_score, best_score=state.best_score,
        completed_at=datetime.now(timezone.utc),
        **fields,
    )


async def _run_step(
    run_id, *, step_id, step_no: int, spec: RunSpec, train, val,
    store: OptimizationStore, seams: Seams, publish: Publisher,
    cancel_event: asyncio.Event, state: _State, edit_budget: int,
    policy: StopPolicy,
) -> float | None:
    config = spec.config
    # This run's algorithm settings, resolved once. Every value below used to be
    # read as `config.get(k) or <literal>`, which is two copies of each default
    # and cannot tell "not set" from a deliberate 0 — see `hyperparams.py`.
    params = resolve_algorithm(config)
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
    train_summary = await _rollout(
        batch, spec=spec, skill_files=state.current_files, split="train",
        skill_step_no=state.parent_step_no or 0, store=store, seams=seams,
        publish=publish, cancel_event=cancel_event, step_no=step_no,
        error_share=policy.error_share("train"),
    )
    await store.record_rollout(step_id, train_summary)
    await _publish_rollout(publish, step_no, train_summary)
    state.counters.record("train", refused=train_summary.aborted)

    if train_summary.aborted:
        # Nothing to reflect on, so nothing else in this step is bought: no
        # analyst calls, no candidate, and — the expensive half — no validation
        # split. The alternative is an edit argued from whichever questions the
        # outage happened to spare, which is a gradient pointing at a network
        # problem, and then a whole validation rollout spent measuring it.
        await _finish_unscored_step(
            store, step_id, seams=seams, state=state,
            action="skip", reason="train_errors",
        )
        await publish({
            "type": "gate_done", "step_no": step_no, "action": "skip",
            "reject_reason": "train_errors", "candidate_score": None,
            "current_score": state.current_score, "best_score": state.best_score,
            "from_cache": False,
        })
        return None

    await _check_cancel(run_id, store, cancel_event)

    # --- 2. reflect, aggregate, clip, apply ---------------------------------
    items, ledger = build_analyst_items(
        train_summary.results,
        questions={item.item_key: item.question for item in batch},
        ground_truths={item.item_key: item.ground_truth_response for item in batch},
        budget_chars=params["reflect_budget_chars"],
    )
    outcome = await asyncio.to_thread(
        run_update_stage,
        files=state.current_files,
        skill_dir=spec.skill_name,
        mode=spec.mode,
        items=items,
        client=seams.optimizer,
        edit_budget=edit_budget,
        minibatch_size=params["minibatch_size"],
        analyst_workers=params["analyst_workers"],
        merge_batch_size=params["merge_batch_size"],
        failure_only=params["failure_only"],
        seed=config.get("seed"),
        truncation_by_item=ledger,
        meta_skill_context=state.meta_skill_text,
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
    # What happened to those patches afterwards. Merge and ranking are where a
    # proposed edit most often stops being one, and until these were stored the
    # page went straight from "the analyst asked for this" to "the skill says
    # that" with the deciding calls invisible.
    for call in outcome.stage_calls:
        await store.record_stage_call(
            step_id,
            seq=call.seq, stage=call.stage, level=call.level,
            prompt_system=call.prompt_system, prompt_user=call.prompt_user,
            output=call.output, error=call.error, duration_ms=call.duration_ms,
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
    # Against the parent skill and the *training* answers — the same comparison
    # `GET .../steps/{n}/skill?base=parent` makes, through the same function, so
    # the overview's warning and Part 2's marked lines cannot disagree. Every
    # training answer, not just this batch's: an analyst that memorised one in
    # step 2 has still put it in the skill at step 7.
    leaks = find_answer_leaks(
        state.current_files, candidate, [item.ground_truth_response for item in train]
    )
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
        # A cached score is one this run already measured and accepted as
        # trustworthy — a refused split never reaches the cache — so it counts
        # as a clean validation for the streak.
        state.counters.record("val", refused=False)
    else:
        val_summary = await _rollout(
            val, spec=spec, skill_files=candidate, split="val", skill_step_no=step_no,
            store=store, seams=seams, publish=publish, cancel_event=cancel_event,
            step_no=step_no, error_share=policy.error_share("val"),
        )
        await store.record_rollout(step_id, val_summary)
        await _publish_rollout(publish, step_no, val_summary)
        state.counters.record("val", refused=val_summary.aborted)

        if val_summary.aborted:
            # The candidate is dropped without ever being judged, and the skill
            # in force is untouched. There is no number to gate on: accepting an
            # edit here would mean accepting it on the strength of whichever
            # questions the agent server happened to answer, which is how a
            # rollout failure turns into a permanent change to the skill.
            await _finish_unscored_step(
                store, step_id, seams=seams, state=state,
                action="reject", reason="val_errors",
                # The edits themselves are real and worth reading — the step's
                # diff is how anyone finds out what was thrown away — so
                # everything except the verdict is recorded exactly as an
                # ordinary step records it.
                edit_budget=edit_budget, candidate_hash=candidate_hash,
                n_edits_merged=outcome.n_edits_merged,
                n_edits_ranked=outcome.n_edits_ranked,
                n_edits_applied=outcome.n_edits_applied,
                n_edits_skipped=outcome.n_edits_skipped,
                edit_reports=outcome.reports,
                lines_added=lines_added, lines_removed=lines_removed,
                files_touched=len(stats), n_answer_leaks=len(leaks),
                skill_len=sum(len(text) for text in candidate.values()),
                edit_summary=outcome.edit_summary, tokens=outcome.tokens,
            )
            await publish({
                "type": "gate_done", "step_no": step_no, "action": "reject",
                "reject_reason": "val_errors", "candidate_score": None,
                "current_score": state.current_score, "best_score": state.best_score,
                "from_cache": False,
            })
            return None

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
        metric=params["gate_metric"],
        mixed_weight=params["mixed_weight"],
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
        step_id, status="done", edit_budget=edit_budget,
        workspace_version=await _agent_version(seams),
        gate_action=gate.action, gate_reject_reason=gate.reject_reason,
        candidate_hash=candidate_hash, candidate_from_cache=from_cache,
        n_edits_merged=outcome.n_edits_merged, n_edits_ranked=outcome.n_edits_ranked,
        n_edits_applied=outcome.n_edits_applied, n_edits_skipped=outcome.n_edits_skipped,
        edit_reports=outcome.reports,
        lines_added=lines_added, lines_removed=lines_removed, files_touched=len(stats),
        n_answer_leaks=len(leaks),
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
    return gate.candidate_score


# --- Rollouts ---------------------------------------------------------------


async def _rollout(
    items, *, spec: RunSpec, skill_files, split: str, skill_step_no: int,
    store: OptimizationStore, seams: Seams, publish: Publisher,
    cancel_event: asyncio.Event, step_no: int, error_share: float,
):
    """One split, answered and judged once.

    `score_rollout` refuses to score a batch that too much of failed, and
    refusing is right: a step measured on 60% of its questions is not a smaller
    measurement but an unrepresentative one, and the gate cannot tell the
    difference. What comes back then is a summary with counts and rows but no
    scores, and the caller decides what that costs.

    It used to buy the whole split a second time before giving up, and give up
    by failing the entire run. Both halves were wrong. The retry was the most
    expensive reaction available — a validation split is the priciest thing in
    a step — and the next step is already a retry of the same agent server;
    ending the run threw away an hour of finished, paid-for steps because the
    last five minutes were an outage. A refused rollout now costs its own step,
    and `stopping.py` decides when a run of them has become an outage worth
    stopping for.
    """
    config = spec.config
    done = 0
    total = len(items)

    # Per-question progress, which is the difference between a page that says
    # "step 3 · rollout" for six minutes and one that says how far through those
    # six minutes it is. `rollout_done` fires once, when the whole split has been
    # answered and judged; between the two there was nothing at all, on the
    # longest-running part of every step.
    #
    # Counted here rather than derived from the rows: `run_rollout` fills a
    # pre-sized list and the caller cannot see it until the gather returns.
    async def item_done(_row) -> None:
        nonlocal done
        done += 1
        await publish({
            "type": "rollout_progress", "step_no": step_no, "split": split,
            "done": done, "total": total,
        })

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
        on_progress=item_done,
    )
    summary = score_rollout(
        rows, split=split, skill_step_no=skill_step_no, error_threshold=error_share,
    )
    if summary.aborted:
        # A split that "failed" because the stop button was pressed is not a
        # flaky agent, and letting it count towards an outage streak would end a
        # cancelled run with the wrong story on the page.
        await _check_cancel(spec.id, store, cancel_event)
        log.warning(
            "optimization %s step %s %s rollout refused (%s)",
            spec.id, step_no, split, summary.abort_reason,
        )
    return summary


async def _publish_rollout(publish: Publisher, step_no: int, summary) -> None:
    await publish({
        "type": "rollout_done", "step_no": step_no, "split": summary.split,
        "hard": summary.hard, "soft": summary.soft,
        "activation_rate": summary.activation_rate,
        "n_items": summary.n_items, "n_scored": summary.n_scored,
        "n_agent_error": summary.n_agent_error, "n_judge_error": summary.n_judge_error,
        "latency_min_ms": summary.latency_min_ms,
        "latency_p50_ms": summary.latency_p50_ms,
        "latency_mean_ms": summary.latency_mean_ms,
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
    params = resolve_algorithm(spec.config)
    return select_gate_score(
        summary.hard or 0.0, summary.soft or 0.0,
        params["gate_metric"], params["mixed_weight"],
    )


def _build_scheduler(spec: RunSpec):
    params = resolve_algorithm(spec.config)
    return build_scheduler(
        mode=params["scheduler"],
        max_lr=params["learning_rate"],
        min_lr=params["min_learning_rate"],
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
