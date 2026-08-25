"""Persistence for an optimization run, behind a Protocol the engine can stub.

The engine is the one piece of this feature that must be testable without a
database. It is a loop that runs for the better part of an hour, and the
behaviours worth protecting — a rejected candidate rolls the skill back, a
cancelled run keeps its finished steps, a restarted backend resumes from the
last completed step, the terminal SSE event goes out even when something throws
— are all *control flow*, not SQL. `tests/test_orchestrator.py` established the
pattern for this codebase with its `StubSession`; the same idea applies here, one
level up: the engine talks to `OptimizationStore`, and the tests give it a
recording double.

So nothing in `engine.py` imports SQLAlchemy, and nothing here decides anything.
The dataclasses below are the engine's whole view of the world.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    OptimizationItem,
    OptimizationMinibatch,
    OptimizationResult,
    OptimizationRollout,
    OptimizationRun,
    OptimizationSkill,
    OptimizationStageCall,
    OptimizationStep,
)


# --- What the engine sees ---------------------------------------------------


@dataclass(frozen=True)
class Item:
    """One question in one split, as the run snapshotted it."""

    item_key: str
    question: str
    ground_truth_response: str
    ground_truth_reasoning: str
    question_pk: uuid.UUID | None = None
    source_eval_set_id: uuid.UUID | None = None
    ordinal: int = 0


@dataclass(frozen=True)
class RunSpec:
    """Everything the loop needs to know about the run it is executing."""

    id: uuid.UUID
    mode: str
    skill_name: str
    config: dict
    secrets: dict
    initial_skill: dict[str, str]
    workspace_baseline: dict[str, str] | None
    detector: dict
    num_epochs: int
    batch_size: int
    steps_per_epoch: int
    total_steps: int
    # The agent config version pinned when the run was created. Each step
    # records what it actually saw, so a mid-run deploy is visible.
    workspace_version: str | None = None


@dataclass
class ResultRow:
    """One question answered once, on its way to `optimization_results`."""

    item_key: str
    correlation_id: str
    status: str
    question_pk: uuid.UUID | None = None
    agent_response: str | None = None
    agent_latency_ms: int | None = None
    verdict: str | None = None
    judge_score: float | None = None
    judge_comment: str | None = None
    failure_kind: str | None = None
    error_message: str | None = None
    activated: bool | None = None
    skills_read: list[str] | None = None
    detector_hit: str | None = None
    # Whether the agent answered from the files we sent rather than its own.
    # Only the pre-flight asks — it is the one call that ships a distinguishing
    # marker — so `None` here means "not asked", which is also what every
    # scored rollout reports. None must never read as a negative: a verdict
    # nobody sought cannot be evidence of anything.
    override_verified: bool | None = None
    trace_ready: bool = False
    trace_error: str | None = None
    minibatch_no: int | None = None
    started_at: Any = None
    # The trace itself, in memory only — never persisted, and deliberately not
    # among the columns `DbOptimizationStore.record_rollout` writes. The reflect
    # stage needs the spans of the rollout it is reflecting on, and re-fetching
    # them from Langfuse a minute later would be a second round of network calls
    # for data we already have. Detail views read the trace live, the same way
    # the evaluation pages do, so nothing depends on this outliving the step.
    trace: Any = None


@dataclass
class RolloutSummary:
    """The aggregate behind one point on the chart.

    Every figure excludes items that failed for infrastructure reasons, and the
    counts sit beside the scores so that exclusion is visible rather than
    implied. See `adapter.score_rollout`, which computes these.
    """

    split: str
    skill_step_no: int
    n_items: int = 0
    n_scored: int = 0
    n_agent_error: int = 0
    n_judge_error: int = 0
    hard: float | None = None
    soft: float | None = None
    activation_rate: float | None = None
    n_activated: int = 0
    latency_min_ms: int | None = None
    latency_p50_ms: int | None = None
    latency_mean_ms: int | None = None
    latency_max_ms: int | None = None
    aborted: bool = False
    abort_reason: str | None = None
    results: list[ResultRow] = field(default_factory=list)


@dataclass
class ResumeState:
    """Where a restarted run picks up, rebuilt from what is already on disk.

    A run is checkpointed per step, so resuming is not a matter of "carry on" —
    the loop's whole working state has to be reconstructed: which skill was in
    force, what it scored, which step holds the best one, and which candidates
    have already been measured. Nothing here is stored as a blob; it is derived
    from the steps and skills that were written as the run went, so a resumed
    run cannot disagree with the chart the developer is looking at.
    """

    last_step_no: int | None
    current_files: dict[str, str]
    current_score: float
    best_files: dict[str, str]
    best_score: float
    best_step: int
    parent_step_no: int | None
    # skill hash → (hard, soft, activation), so a candidate already measured
    # before the restart does not cost a second validation split.
    score_cache: dict[str, tuple[float, float, float | None]] = field(default_factory=dict)
    # How many rollouts in a row the run had already had to refuse when it was
    # interrupted, per split (`optimizer/stopping.py`). Derived like everything
    # else here rather than stored: a run that stops early because its agent
    # server is down must reach that conclusion whether or not the backend
    # restarted in the middle of the outage.
    train_error_streak: int = 0
    val_error_streak: int = 0


class OptimizationStore(Protocol):
    """What the engine needs from storage. Implemented below, stubbed in tests."""

    async def load_run(self, run_id: uuid.UUID) -> RunSpec | None: ...

    async def load_items(self, run_id: uuid.UUID, split: str) -> list[Item]: ...

    async def last_completed_step(self, run_id: uuid.UUID) -> int | None: ...

    async def load_resume_state(self, run_id: uuid.UUID) -> ResumeState | None: ...

    async def cancel_requested(self, run_id: uuid.UUID) -> bool: ...

    async def load_val_results(self, run_id: uuid.UUID, step_no: int) -> list[dict]: ...

    async def set_status(self, run_id: uuid.UUID, status: str, **fields: Any) -> None: ...

    async def start_step(
        self, run_id: uuid.UUID, *, step_no: int, epoch_no: int,
        step_in_epoch: int, parent_step_no: int | None,
    ) -> uuid.UUID: ...

    async def record_rollout(
        self, step_id: uuid.UUID, summary: RolloutSummary
    ) -> uuid.UUID: ...

    async def record_minibatch(self, step_id: uuid.UUID, **fields: Any) -> None: ...

    async def record_stage_call(self, step_id: uuid.UUID, **fields: Any) -> None: ...

    async def record_skill(
        self, run_id: uuid.UUID, *, step_no: int, kind: str,
        files: dict[str, str], content_hash: str, per_file_stats: dict,
    ) -> None: ...

    async def finish_step(self, step_id: uuid.UUID, **fields: Any) -> None: ...

    async def finish_run(self, run_id: uuid.UUID, **fields: Any) -> None: ...


# --- The SQLAlchemy implementation -----------------------------------------


class DbOptimizationStore:
    """`OptimizationStore` over a live session.

    One session for the whole run, like the orchestrator's. Every write commits
    immediately: a step is the checkpoint granularity, so an interrupted run must
    find its finished steps on disk rather than in a transaction that died with
    the process.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def load_run(self, run_id: uuid.UUID) -> RunSpec | None:
        run = await self.session.get(OptimizationRun, run_id)
        if run is None:
            return None
        return RunSpec(
            id=run.id,
            mode=run.mode,
            skill_name=run.skill_name,
            config=run.config or {},
            secrets=run.secrets or {},
            initial_skill=dict(run.initial_skill or {}),
            workspace_baseline=dict(run.workspace_baseline) if run.workspace_baseline else None,
            workspace_version=run.workspace_version,
            detector=dict(run.detector or {}),
            num_epochs=run.num_epochs,
            batch_size=run.batch_size,
            steps_per_epoch=run.steps_per_epoch,
            total_steps=run.total_steps,
        )

    async def load_items(self, run_id: uuid.UUID, split: str) -> list[Item]:
        rows = (
            await self.session.scalars(
                select(OptimizationItem)
                .where(OptimizationItem.run_id == run_id, OptimizationItem.split == split)
                .order_by(OptimizationItem.ordinal, OptimizationItem.item_key)
            )
        ).all()
        return [
            Item(
                item_key=row.item_key,
                question=row.question,
                ground_truth_response=row.ground_truth_response,
                ground_truth_reasoning=row.ground_truth_reasoning,
                question_pk=row.question_pk,
                source_eval_set_id=row.source_eval_set_id,
                ordinal=row.ordinal,
            )
            for row in rows
        ]

    async def last_completed_step(self, run_id: uuid.UUID) -> int | None:
        """The highest step that finished, for resuming after a restart.

        'Finished' means `status='done'`: a step left mid-flight is re-run from
        the top rather than patched up, because half a rollout is not a gradient.
        """
        return await self.session.scalar(
            select(func.max(OptimizationStep.step_no)).where(
                OptimizationStep.run_id == run_id, OptimizationStep.status == "done"
            )
        )

    async def load_resume_state(self, run_id: uuid.UUID) -> ResumeState | None:
        """Rebuild the loop's working state from the steps already on disk.

        Walks the completed steps in order and replays their gate decisions —
        which is how `current` ends up being the last *accepted* candidate
        rather than the last one produced. Deriving it beats storing it: a
        stored pointer can disagree with the steps around it after a crash
        between two writes, and then a resumed run would continue from a skill
        the chart never shows.

        The validation scores come from the rollouts rather than from
        `steps.current_score`, because a rejected step's own score is not on the
        step row — the gate leaves `current_score` at the value that survived.
        """
        steps = (
            await self.session.scalars(
                select(OptimizationStep)
                .where(OptimizationStep.run_id == run_id, OptimizationStep.status == "done")
                .order_by(OptimizationStep.step_no)
            )
        ).all()
        if not steps:
            return None

        skills = (
            await self.session.scalars(
                select(OptimizationSkill).where(OptimizationSkill.run_id == run_id)
            )
        ).all()
        files_by_step = {(s.step_no, s.kind): dict(s.files or {}) for s in skills}

        val_scores: dict[uuid.UUID, OptimizationRollout] = {
            row.step_id: row
            for row in (
                await self.session.scalars(
                    select(OptimizationRollout)
                    .join(OptimizationStep, OptimizationStep.id == OptimizationRollout.step_id)
                    .where(
                        OptimizationStep.run_id == run_id,
                        OptimizationRollout.split == "val",
                    )
                )
            ).all()
        }

        initial = files_by_step.get((0, "initial"), {})
        state = ResumeState(
            last_step_no=steps[-1].step_no,
            current_files=dict(initial), current_score=0.0,
            best_files=dict(initial), best_score=0.0, best_step=0,
            parent_step_no=None, score_cache={},
        )

        for step in steps:
            candidate = files_by_step.get((step.step_no, "candidate"))
            rollout = val_scores.get(step.id)
            if step.candidate_hash and rollout is not None:
                state.score_cache[step.candidate_hash] = (
                    float(rollout.hard or 0.0), float(rollout.soft or 0.0),
                    None if rollout.activation_rate is None else float(rollout.activation_rate),
                )
            if step.step_no == 0:
                state.current_score = float(step.current_score or 0.0)
                state.best_score = float(step.best_score or 0.0)
                continue
            state.current_score = float(step.current_score or state.current_score)
            state.best_score = float(step.best_score or state.best_score)
            if step.gate_action in ("accept", "accept_new_best") and candidate is not None:
                state.current_files = candidate
                state.parent_step_no = step.step_no
            if step.gate_action == "accept_new_best" and candidate is not None:
                state.best_files = candidate
                state.best_step = step.step_no
        state.train_error_streak = _trailing_streak(steps, "train_errors")
        state.val_error_streak = _trailing_streak(steps, "val_errors")
        return state

    async def set_status(self, run_id: uuid.UUID, status: str, **fields: Any) -> None:
        await self.finish_run(run_id, status=status, **fields)

    async def cancel_requested(self, run_id: uuid.UUID) -> bool:
        """The durable half of cancellation, re-read each step.

        `app/cancellation.py` holds the fast in-process event; this is what
        survives a restart and what the UI actually wrote to.
        """
        self.session.expire_all()
        return bool(
            await self.session.scalar(
                select(OptimizationRun.cancel_requested).where(OptimizationRun.id == run_id)
            )
        )

    async def start_step(
        self, run_id: uuid.UUID, *, step_no: int, epoch_no: int,
        step_in_epoch: int, parent_step_no: int | None,
    ) -> uuid.UUID:
        step = OptimizationStep(
            run_id=run_id, step_no=step_no, epoch_no=epoch_no,
            step_in_epoch=step_in_epoch, parent_step_no=parent_step_no,
            status="running",
        )
        self.session.add(step)
        await self.session.commit()
        return step.id

    async def record_rollout(
        self, step_id: uuid.UUID, summary: RolloutSummary
    ) -> uuid.UUID:
        rollout = OptimizationRollout(
            step_id=step_id,
            split=summary.split,
            skill_step_no=summary.skill_step_no,
            n_items=summary.n_items,
            n_scored=summary.n_scored,
            n_agent_error=summary.n_agent_error,
            n_judge_error=summary.n_judge_error,
            hard=summary.hard,
            soft=summary.soft,
            activation_rate=summary.activation_rate,
            n_activated=summary.n_activated,
            latency_min_ms=summary.latency_min_ms,
            latency_p50_ms=summary.latency_p50_ms,
            latency_mean_ms=summary.latency_mean_ms,
            latency_max_ms=summary.latency_max_ms,
            aborted=summary.aborted,
            abort_reason=summary.abort_reason,
        )
        self.session.add(rollout)
        await self.session.flush()
        for row in summary.results:
            self.session.add(
                OptimizationResult(
                    rollout_id=rollout.id,
                    item_key=row.item_key,
                    question_pk=row.question_pk,
                    correlation_id=row.correlation_id,
                    agent_response=row.agent_response,
                    agent_latency_ms=row.agent_latency_ms,
                    verdict=row.verdict,
                    judge_score=row.judge_score,
                    judge_comment=row.judge_comment,
                    status=row.status,
                    failure_kind=row.failure_kind,
                    error_message=row.error_message,
                    activated=row.activated,
                    skills_read=row.skills_read,
                    detector_hit=row.detector_hit,
                    trace_ready=row.trace_ready,
                    trace_error=row.trace_error,
                    minibatch_no=row.minibatch_no,
                    started_at=row.started_at,
                )
            )
        await self.session.commit()
        return rollout.id

    async def record_minibatch(self, step_id: uuid.UUID, **fields: Any) -> None:
        """The analyst call, and the number stamped on the questions it consumed.

        `item_keys` is not a column — it is the link written back onto
        `optimization_results.minibatch_no`, which cannot be set when the
        rollout row is created because the split into minibatches does not exist
        until the reflect stage has seen the results. Nothing else records which
        failures an analyst was shown together, so without this the grouping on
        Part 1 would have to be invented at read time.

        Scoped to this step's *training* rollout. Not to the run: every step
        holds the same item keys, so a run-wide update would renumber every
        earlier step on each new one. Not to both splits: an overlapping
        question has a row in each, and numbering the validation row would show
        held-out data as evidence for an edit.
        """
        item_keys = fields.pop("item_keys", None)
        self.session.add(OptimizationMinibatch(step_id=step_id, **fields))
        if item_keys:
            await self.session.execute(
                update(OptimizationResult)
                .where(
                    OptimizationResult.item_key.in_(item_keys),
                    OptimizationResult.rollout_id.in_(
                        select(OptimizationRollout.id).where(
                            OptimizationRollout.step_id == step_id,
                            OptimizationRollout.split == "train",
                        )
                    ),
                )
                .values(minibatch_no=fields.get("minibatch_no"))
            )
        await self.session.commit()

    async def record_stage_call(self, step_id: uuid.UUID, **fields: Any) -> None:
        """One merge or ranking call, as it was sent and as it came back.

        A plain insert: unlike a minibatch there is nothing to stamp back onto
        the results, because these stages see patches rather than questions —
        which is exactly why they need recording. An edit that no analyst is
        blamed for and no skill contains was lost in one of these.
        """
        self.session.add(OptimizationStageCall(step_id=step_id, **fields))
        await self.session.commit()

    async def load_val_results(self, run_id: uuid.UUID, step_no: int) -> list[dict]:
        """One step's validation results, shaped as the slow update expects them.

        Read back from storage rather than carried in memory: a resumed run has
        to be able to compare across an epoch boundary whose first half it never
        executed, and the rows are already on disk.
        """
        rows = (
            await self.session.execute(
                select(
                    OptimizationResult.item_key,
                    OptimizationResult.verdict,
                    OptimizationResult.judge_score,
                    OptimizationResult.agent_response,
                    OptimizationResult.judge_comment,
                )
                .join(
                    OptimizationRollout,
                    OptimizationRollout.id == OptimizationResult.rollout_id,
                )
                .join(OptimizationStep, OptimizationStep.id == OptimizationRollout.step_id)
                .where(
                    OptimizationStep.run_id == run_id,
                    OptimizationStep.step_no == step_no,
                    OptimizationRollout.split == "val",
                )
            )
        ).all()
        return [
            {
                "id": item_key,
                "hard": 1 if verdict == "correct" else 0,
                "soft": float(score) if score is not None else 0.0,
                "predicted_answer": response or "",
                "fail_reason": comment or "",
            }
            for item_key, verdict, score, response, comment in rows
        ]

    async def record_skill(
        self, run_id: uuid.UUID, *, step_no: int, kind: str,
        files: dict[str, str], content_hash: str, per_file_stats: dict,
    ) -> None:
        self.session.add(
            OptimizationSkill(
                run_id=run_id, step_no=step_no, kind=kind, files=files,
                content_hash=content_hash, per_file_stats=per_file_stats,
            )
        )
        await self.session.commit()

    async def finish_step(self, step_id: uuid.UUID, **fields: Any) -> None:
        step = await self.session.get(OptimizationStep, step_id)
        if step is None:
            return
        for key, value in fields.items():
            setattr(step, key, value)
        await self.session.commit()

    async def finish_run(self, run_id: uuid.UUID, **fields: Any) -> None:
        run = await self.session.get(OptimizationRun, run_id)
        if run is None:
            return
        for key, value in fields.items():
            setattr(run, key, value)
        await self.session.commit()


def _trailing_streak(steps, reason: str) -> int:
    """How many of the last steps in a row were refused for *reason*.

    The engine's counters, rebuilt from what is on disk. Trailing rather than
    total, because that is what the counters mean: three refusals in a row are
    an agent server that has stopped answering, three spread over a long run are
    a flaky afternoon (`optimizer/stopping.py`).

    A refused step carries the reason on `gate_reject_reason` — the same column
    an ordinary rejection uses — so nothing new is stored to make this
    derivable. What has to be got right is which steps a split's streak may
    *skip*: a step whose training batch never came back never reaches its
    validation rollout, so it says nothing either way about whether validation
    is answering, and treating it as a clean validation would hand a broken
    agent server a fresh three steps every time the training half failed too.
    Step 0 has no training rollout at all, for the same reason.
    """
    streak = 0
    for step in reversed(steps):
        if step.gate_reject_reason == reason:
            streak += 1
        elif reason == "val_errors" and step.gate_reject_reason == "train_errors":
            continue  # never got as far as a validation rollout
        elif reason == "train_errors" and step.step_no == 0:
            continue  # the baseline answers validation only
        else:
            break
    return streak
