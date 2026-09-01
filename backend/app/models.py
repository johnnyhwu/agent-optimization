"""SQLAlchemy ORM models — mirror the §6.14 schema exactly.

The DB schema is owned by the Alembic migration (0001_stage1_schema.py); these
models map onto those tables. Note `EvalSet.meta` maps to the DB column
`metadata` because `metadata` is reserved on the declarative Base.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class EvalSet(Base):
    __tablename__ = "eval_sets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_format: Mapped[str] = mapped_column(Text, nullable=False)  # 'csv' | 'jsonl'
    # ORM attr `meta` -> DB column `metadata` (reserved name on Base).
    meta: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    # How this set's answers are graded (owner-only; see services/judge_prompt).
    # NULL means "the code's default", deliberately rather than a copy of it: a
    # set that never overrode the prompt should inherit later improvements to it.
    judge_system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    judge_user_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set by the verify button, cleared the moment either prompt changes — a
    # stale "verified" badge is worse than none.
    judge_prompt_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    judge_prompt_verified_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    # When an owner last opened the judging settings. Drives the "look at this"
    # badge on a newly created set: the badge means "nobody has confirmed the
    # grading criteria", not "your prompt is the default one" — the latter is
    # true of almost every set and would be ignored within a week.
    judge_prompt_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)

    questions: Mapped[list["Question"]] = relationship(
        back_populates="eval_set", cascade="all, delete-orphan"
    )
    runs: Mapped[list["Run"]] = relationship(
        back_populates="eval_set", cascade="all, delete-orphan"
    )
    roles: Mapped[list["EvalSetRole"]] = relationship(
        back_populates="eval_set", cascade="all, delete-orphan"
    )
    # Present only for sets built by running an uploaded Python script.
    script: Mapped["EvalSetScript | None"] = relationship(
        back_populates="eval_set", cascade="all, delete-orphan", uselist=False
    )


class EvalSetScript(Base):
    """The Python script an eval set was generated from, kept for provenance.

    A table of its own rather than columns on `eval_sets` for one concrete
    reason: `_build_cards` in routers/eval_sets.py reads a page of sets to render
    the home page, and it was deliberately rewritten to touch a bounded number of
    rows. Hanging a full script body off every row of that query would undo it,
    to display something the home page never shows.

    **There is no password column, and there must never be one.** The credentials
    a script ran with are supplied per run, used, and forgotten; what is recorded
    is where it connected and as whom, which is what someone auditing or
    reproducing the set actually needs.
    """

    __tablename__ = "eval_set_scripts"
    # One script per set: a set is locked at creation (§6.11), so there is exactly
    # one run that produced it and no way to add a second.
    __table_args__ = (UniqueConstraint("eval_set_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    eval_set_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("eval_sets.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    # sha256 of the source, so two sets built from the same script are visibly the
    # same without diffing two bodies of text.
    source_sha256: Mapped[str] = mapped_column(Text, nullable=False)

    db_host: Mapped[str] = mapped_column(Text, nullable=False)
    db_port: Mapped[int] = mapped_column(Integer, nullable=False)
    db_name: Mapped[str] = mapped_column(Text, nullable=False)
    db_user: Mapped[str] = mapped_column(Text, nullable=False)

    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    executed_by: Mapped[str] = mapped_column(Text, nullable=False)
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )

    eval_set: Mapped["EvalSet"] = relationship(back_populates="script")


class Question(Base):
    __tablename__ = "questions"
    __table_args__ = (UniqueConstraint("eval_set_id", "question_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    eval_set_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("eval_sets.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[str] = mapped_column(Text, nullable=False)  # §6.11 immutable
    question: Mapped[str] = mapped_column(Text, nullable=False)
    ground_truth_response: Mapped[str] = mapped_column(Text, nullable=False)
    ground_truth_reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)

    eval_set: Mapped["EvalSet"] = relationship(back_populates="questions")
    skills: Mapped[list["QuestionSkill"]] = relationship(
        back_populates="question", cascade="all, delete-orphan", order_by="QuestionSkill.ordinal"
    )


class QuestionSkill(Base):
    __tablename__ = "question_skills"
    __table_args__ = (PrimaryKeyConstraint("question_pk", "ordinal"),)

    question_pk: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
    )
    skill_name: Mapped[str] = mapped_column(Text, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)  # Stage 1: only 0 used

    question: Mapped["Question"] = relationship(back_populates="skills")


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    eval_set_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("eval_sets.id", ondelete="CASCADE"), nullable=False
    )
    triggered_by: Mapped[str] = mapped_column(Text, nullable=False)  # token subject
    # Developer-supplied label; the UI falls back to started_at when unset.
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The configuration this run was triggered with (§9.2 seams). Split in two so
    # that "credentials never leave the server" is structural: no response model
    # reads `secrets`. Blank/missing keys fall back to the environment.
    config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    secrets: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)  # running|completed|failed|cancelled
    # Set by POST /runs/{id}/cancel. Persisted rather than in-memory only, so a
    # run that was stopped still reads as stopped after a backend restart.
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    # The agent's version when the run started and when it ended. A run is a
    # measurement whose pass rate gets compared against other runs, and that
    # only holds if the agent held still; a redeploy halfway through makes the
    # questions either side of it readings of two different systems, and the
    # only other symptom is the number moving — which is what the comparison is
    # for. NULL is "not known" (fake mode, an agent that did not answer, a run
    # that predates this), never "unchanged": only the two disagreeing is a
    # signal, so one of them missing means there is nothing to say.
    workspace_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    workspace_version_end: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pass_rate: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    total_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    correct_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Why a run ended as status='failed' (unexpected orchestrator error).
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    eval_set: Mapped["EvalSet"] = relationship(back_populates="runs")
    results: Mapped[list["QuestionResult"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class QuestionResult(Base):
    __tablename__ = "question_results"
    __table_args__ = (UniqueConstraint("run_id", "question_pk"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    question_pk: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id"), nullable=False
    )
    correlation_id: Mapped[str] = mapped_column(Text, nullable=False)  # -> Langfuse trace
    # What the agent actually answered — the other half of "the eval result",
    # next to the judge's verdict on it.
    agent_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    verdict: Mapped[str | None] = mapped_column(Text, nullable=True)  # correct|incorrect
    judge_score: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    judge_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)  # pending|done|failed|cancelled
    # Why status='failed' (agent error, judge error, timeout, ...).
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Which step failed, as a machine-readable kind: 'agent' | 'judge' |
    # 'judge_invalid'. The message alone already said this, but only in prose —
    # and one of these is not like the others: 'judge_invalid' means the judge
    # replied and we could not parse it, which usually indicts the eval set's
    # judge prompt rather than the agent. Lumping it in with a timeout hides the
    # one failure the owner can actually fix.
    failure_kind: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # How many model calls the agent spent on this question, counted from its
    # trace while the run was executing. Two questions that both took nine
    # seconds are not the same question if one made a single call and the other
    # made eleven, and that is the first thing worth knowing when a run turns
    # expensive. NULL on rows written before this column existed — the traces
    # behind them are no longer ours to re-read, and a 0 would claim the agent
    # called nothing rather than that nobody was counting.
    llm_call_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # When this question's agent call went out — what the left column's timer
    # counts from. Distinct from `created_at` below, which is when the row was
    # written: the orchestrator creates every row for a run up front, so at
    # RUN_CONCURRENCY=1 the last question's row is minutes older than its own
    # first call. NULL on rows written before this column existed.
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    trace_ready: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # Why the trace could not be fetched (Langfuse unreachable / 401 / timeout).
    # Distinguishes a misconfigured trace store from ingestion that simply hasn't
    # landed yet — the UI shows the same "generating" state for both otherwise.
    trace_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Why the diagnosis LLM call failed. A failed diagnosis never fails the
    # question (the verdict is the result), but it must not be invisible either.
    diagnosis_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)

    run: Mapped["Run"] = relationship(back_populates="results")
    question: Mapped["Question"] = relationship()
    analysis: Mapped["SpanAnalysis | None"] = relationship(
        back_populates="question_result", cascade="all, delete-orphan", uselist=False
    )


class SpanAnalysis(Base):
    __tablename__ = "span_analyses"
    __table_args__ = (UniqueConstraint("question_result_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    question_result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("question_results.id", ondelete="CASCADE"),
        nullable=False,
    )
    overall_diagnosis: Mapped[str] = mapped_column(Text, nullable=False)
    caveat: Mapped[str | None] = mapped_column(Text, nullable=True)  # §6.8, own column
    raw_llm_output: Mapped[dict] = mapped_column(JSONB, nullable=False)  # full JSON incl suspects[]
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    model_used: Mapped[str] = mapped_column(Text, nullable=False)

    question_result: Mapped["QuestionResult"] = relationship(back_populates="analysis")


class EvalSetRole(Base):
    __tablename__ = "eval_set_roles"
    __table_args__ = (PrimaryKeyConstraint("eval_set_id", "user_subject"),)

    eval_set_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("eval_sets.id", ondelete="CASCADE"), nullable=False
    )
    user_subject: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)  # owner | viewer

    eval_set: Mapped["EvalSet"] = relationship(back_populates="roles")


# --- Optimize (Stage 3) -----------------------------------------------------
#
# Seven tables, all new, none of them touching the seven above. That separation
# is the point: an optimization run performs epochs × steps × (train + val)
# agent calls and records a verdict for each, which is the same *shape* as eval
# data and would wreck an eval set's card, sparkline and regression summary if it
# reached them. The eval side is deliberately tuned to touch a bounded number of
# rows (docs/spec.md §10.2③); a join from here would undo that.
#
# So Optimize points *at* the existing tables by id and they never point back —
# there is no relationship, no back_populates, and no foreign key that cascades
# from an eval set into an optimization run. `tests/test_optimizer_isolation.py`
# is the guard.
#
# The links that do exist are `ondelete="SET NULL"`, not CASCADE, and every row
# that needs a question carries its own snapshot of the text. A run is a
# historical record: deleting a source eval set next month must leave last
# month's optimization readable, and unlike `runs` an optimization run belongs to
# no single set — it can source questions from several.


class OptimizationRun(Base):
    __tablename__ = "optimization_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    # pending | running | completed | failed | cancelled | interrupted.
    # 'interrupted' is the one an eval run has no equivalent of: optimization is
    # checkpointed per step, so a backend restart leaves something resumable
    # rather than something to write off.
    status: Mapped[str] = mapped_column(Text, nullable=False)
    # isolated | routing. Decides what is sent to the agent (one skill or all of
    # them), which analyst prompts run, what the gate additionally guards, and
    # which half of SKILL.md the optimizer may edit.
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    # The first target, and the only one for an isolated run. Kept as its own
    # column because every screen, download name and log line reads it, and
    # because every run created before routing could take several has one.
    skill_name: Mapped[str] = mapped_column(Text, nullable=False)
    # Every skill this run may edit, `skill_name` included. Null on runs that
    # predate multiple targets, which read as `[skill_name]` — so nothing has to
    # be backfilled and an old run resumes unchanged.
    target_skills: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # Same split as `runs`: no response model reads `secrets`, which is what
    # makes "credentials never leave the server" structural rather than a habit.
    config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    secrets: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # What the agent server reported when the run started. A run is optimising a
    # snapshot; if the agent moves underneath it, the numbers still describe the
    # snapshot and the UI says the workspace has since changed.
    workspace_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    # {relative path: text} for the skill being optimised, pinned at run start.
    initial_skill: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # Every *other* skill, for routing mode only — it sends the whole directory,
    # so those files are part of the experiment and must not shift mid-run.
    workspace_baseline: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Activation-detector configuration and what the pre-flight rollout saw.
    detector: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    num_epochs: Mapped[int] = mapped_column(Integer, nullable=False)
    batch_size: Mapped[int] = mapped_column(Integer, nullable=False)
    steps_per_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    total_steps: Mapped[int] = mapped_column(Integer, nullable=False)

    best_step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    best_score: Mapped[float | None] = mapped_column(Numeric, nullable=True)

    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Why the loop ended, which `status` cannot say: finished | cancelled |
    # failed | early_stop_train_errors | early_stop_val_errors |
    # early_stop_patience | early_stop_target. A run that stopped because
    # validation reached its target and one that ran out of steps are both
    # 'completed', and the difference is the run's whole result. Null on runs
    # that finished before early stopping existed, and on runs still going.
    # See `app/optimizer/stopping.py`.
    stop_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    items: Mapped[list["OptimizationItem"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    steps: Mapped[list["OptimizationStep"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="OptimizationStep.step_no"
    )
    skills: Mapped[list["OptimizationSkill"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class OptimizationItem(Base):
    """One question, in one split, as it was when the run started.

    The snapshot columns are not denormalisation for speed — they are the same
    rule `runs` follows (orchestrator.py snapshots the question set at run
    start): editing a question tomorrow must not change what an optimization
    that finished today was measuring.

    A question can legitimately appear in **both** splits: the wizard offers
    "duplicate to validation" deliberately. That weakens the gate, which the UI
    warns about — it is the developer's call, not the schema's.
    """

    __tablename__ = "optimization_items"
    __table_args__ = (UniqueConstraint("run_id", "split", "item_key"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("optimization_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    split: Mapped[str] = mapped_column(Text, nullable=False)  # train | val
    # `question_id` is unique per eval set, not globally (see the UniqueConstraint
    # on `questions`), and a run can import from several sets — so the id handed
    # to the algorithm is composite. Two sets holding "q_1" is routine after a
    # download-edit-re-upload cycle, and collapsing them would silently merge two
    # different questions into one training item.
    item_key: Mapped[str] = mapped_column(Text, nullable=False)
    question_pk: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="SET NULL"), nullable=True
    )
    source_eval_set_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("eval_sets.id", ondelete="SET NULL"), nullable=True
    )

    question: Mapped[str] = mapped_column(Text, nullable=False)
    ground_truth_response: Mapped[str] = mapped_column(Text, nullable=False)
    ground_truth_reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    # The skill tags this question carried when the run started — what routing
    # accuracy scores the agent's choice against. Snapshotted for the same
    # reason as the question itself, and nullable because every run created
    # before routing accuracy existed has none: null is "this run did not
    # record them", which is not the same claim as the empty list.
    ground_truth_skills: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    # What the picker showed when this run was configured, frozen. The live
    # figure moves with every later eval run, so without this nobody can answer
    # "why did I put this question in the training split?" a week later.
    prior_accuracy: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    prior_runs: Mapped[int | None] = mapped_column(Integer, nullable=True)

    run: Mapped["OptimizationRun"] = relationship(back_populates="items")


class OptimizationStep(Base):
    """One turn of the loop: rollout → reflect → aggregate → select → update → gate.

    `step_no = 0` is the baseline — the initial skill measured on the validation
    split before anything is edited. Without it the chart cannot answer whether
    the run helped at all.
    """

    __tablename__ = "optimization_steps"
    __table_args__ = (UniqueConstraint("run_id", "step_no"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("optimization_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_no: Mapped[int] = mapped_column(Integer, nullable=False)
    epoch_no: Mapped[int] = mapped_column(Integer, nullable=False)
    step_in_epoch: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # The last step whose candidate was *accepted* — the diff's baseline. Not
    # `step_no - 1`: a rejected step rolls the skill back, so the parent of step 4
    # may well be step 2.
    parent_step_no: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[str] = mapped_column(Text, nullable=False)  # running|done|aborted
    # Historical. Runs made before early stopping bought a whole split a second
    # time when too much of it failed, and this recorded that it had happened.
    # Nothing writes it now — a refused rollout costs its step and the run
    # carries on (`app/optimizer/stopping.py`) — but the rows that carry it are
    # still explaining their own noise, so the column and its badge stay.
    retried: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    abort_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    edit_budget: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # accept_new_best | accept | reject | force_accept | skip
    gate_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    # accuracy | activation — which guard refused it. Routing mode adds the
    # second, and "rejected" alone would not say which.
    gate_reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Upstream caches validation scores by skill hash: a step whose edits were all
    # skipped produces a candidate identical to the current skill, and re-running
    # the whole validation split to learn that would be pure waste.
    candidate_from_cache: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    n_edits_merged: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_edits_ranked: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_edits_applied: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_edits_skipped: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Upstream's per-edit apply report: `{op, path, target, content_preview,
    # status}` for every edit that was proposed. The count above cannot say
    # whether an edit was skipped because it named a protected region, a path
    # outside the skill, or a target string that did not exist — three different
    # problems — and the status is decided inside `apply_patch_with_report`, so
    # it cannot be recomputed later from the snapshots. Bounded by construction:
    # the edit budget is single digits and both text fields are clipped to 200.
    edit_reports: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    lines_added: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lines_removed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    files_touched: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Training gold answers this step copied verbatim into the skill, measured
    # against the parent snapshot — the same comparison Part 2 shows. Counted
    # when the candidate is written rather than when the overview is read: the
    # search is a diff per step, and that page reloads while the run streams.
    n_answer_leaks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The agent's config version as it was when this step ran. A run is a
    # comparison, and it only holds if the other side does: a deploy to the agent
    # server halfway through makes the steps before and after it measurements of
    # different systems, and nothing else about the run would ever show it.
    # NULL when the workspace seam is off or the probe failed — not "", which
    # would read as disagreeing with every pinned version.
    workspace_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    skill_len: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The optimizer's own account of what it changed — the tooltip's second half.
    edit_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Routing only. The analyst's own answer to "this is not the descriptions'
    # fault" — the agent is instructed to answer without consulting a skill,
    # most often. A run whose every step reports "0 edits applied" has a reason,
    # and until this column the reason was inside a minibatch's raw JSON.
    routing_blocked_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Routing only, and set only when the batch's questions genuinely ran under
    # different agent setups — a moved timestamp is not that. The step scored
    # them as one system, which is a fact about the measurement rather than
    # about the skill. `{n_prompts, n_variants, majority_share}`.
    setup_divergence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    current_score: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    best_score: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    timings: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    tokens: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    run: Mapped["OptimizationRun"] = relationship(back_populates="steps")
    rollouts: Mapped[list["OptimizationRollout"]] = relationship(
        back_populates="step", cascade="all, delete-orphan"
    )
    minibatches: Mapped[list["OptimizationMinibatch"]] = relationship(
        back_populates="step", cascade="all, delete-orphan",
        order_by="OptimizationMinibatch.minibatch_no",
    )
    stage_calls: Mapped[list["OptimizationStageCall"]] = relationship(
        back_populates="step", cascade="all, delete-orphan",
        order_by="OptimizationStageCall.seq",
    )


class OptimizationRollout(Base):
    """One split measured once — the aggregate behind a single point on the chart.

    Every figure here excludes the items that failed for infrastructure reasons.
    An agent timeout is not the skill being wrong, and counting it as a wrong
    answer would feed the optimizer a gradient pointing at a network problem.
    The counts are kept beside the scores so the exclusion is visible rather than
    implied.
    """

    __tablename__ = "optimization_rollouts"
    __table_args__ = (UniqueConstraint("step_id", "split"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    step_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("optimization_steps.id", ondelete="CASCADE"),
        nullable=False,
    )
    split: Mapped[str] = mapped_column(Text, nullable=False)  # train | val
    # Which step's skill was in the agent's hands for this rollout. On the train
    # side that is the *parent* step's skill, because train is measured before
    # this step's edit and validation after it.
    skill_step_no: Mapped[int] = mapped_column(Integer, nullable=False)

    n_items: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    n_scored: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    n_agent_error: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    n_judge_error: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    # The judge's verdict on the answers. What an isolated run is gated on, and
    # what a routing run still measures and plots without gating on it.
    hard: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    soft: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    # Whether the agent reached for the skills the question was tagged with:
    # `routing_hard` is the strict set match, `routing_soft` the F1 over the two
    # sets. What a routing run is gated on, and meaningful only there — an
    # isolated run over tagged questions writes real numbers here, because the
    # scoring path is deliberately one path in both modes, and nothing reads
    # them. Null when nothing could be measured and on every run that predates
    # the columns — never zero, which would read as "it routed everything
    # wrong".
    routing_hard: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    routing_soft: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    activation_rate: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    n_activated: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    latency_min_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_p50_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_max_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The mean, stored rather than derived. It cannot be recovered from the three
    # above — median and mean answer different questions, and the gap between
    # them is exactly what says whether a slow rollout was slow throughout or was
    # one question hanging until the timeout. Recomputing it from the results
    # would work only for as long as they are all still on disk.
    latency_mean_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    aborted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    abort_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    step: Mapped["OptimizationStep"] = relationship(back_populates="rollouts")
    results: Mapped[list["OptimizationResult"]] = relationship(
        back_populates="rollout", cascade="all, delete-orphan"
    )


class OptimizationResult(Base):
    """One question answered once. The optimization twin of `question_results`.

    Same vocabulary on purpose — `status`, `failure_kind`, `correlation_id`,
    `trace_ready` mean exactly what they mean there, so the trace endpoint can
    return the same `TraceView` shape and the browser can reuse the span viewer
    unchanged.
    """

    __tablename__ = "optimization_results"
    __table_args__ = (UniqueConstraint("rollout_id", "item_key"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    rollout_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("optimization_rollouts.id", ondelete="CASCADE"),
        nullable=False,
    )
    item_key: Mapped[str] = mapped_column(Text, nullable=False)
    question_pk: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="SET NULL"), nullable=True
    )
    correlation_id: Mapped[str] = mapped_column(Text, nullable=False)

    agent_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    verdict: Mapped[str | None] = mapped_column(Text, nullable=True)
    judge_score: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    judge_comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(Text, nullable=False)  # pending|done|failed
    failure_kind: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Did the agent actually load this skill? NULL means the detectors could not
    # tell — which is a third answer, not a false, and the run says so rather
    # than reporting 0% activation as if the agent had ignored the skill.
    activated: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Every skill the trace shows being read, not just the target one. In routing
    # mode "it read `reporting` instead of you" is a far stronger signal for the
    # analyst than "it did not read you".
    skills_read: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    detector_hit: Mapped[str | None] = mapped_column(Text, nullable=True)

    trace_ready: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    trace_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Which reflect minibatch this item fed, so Part 1 can group the list the way
    # the algorithm actually consumed it. NULL on the validation split, which is
    # never reflected on.
    minibatch_no: Mapped[int | None] = mapped_column(Integer, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )

    rollout: Mapped["OptimizationRollout"] = relationship(back_populates="results")


class OptimizationMinibatch(Base):
    """One analyst call: the gradient, and the evidence it was computed from.

    This table exists because "why did the optimizer propose that?" is otherwise
    unanswerable. It holds the prompt that was actually sent — *after* truncation,
    which is what makes it safe to store verbatim: its size is bounded by the
    budget by construction. The original is not kept; the ledger below records
    what was cut and from where, which is the part worth auditing.
    """

    __tablename__ = "optimization_minibatches"
    __table_args__ = (UniqueConstraint("step_id", "minibatch_no"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    step_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("optimization_steps.id", ondelete="CASCADE"),
        nullable=False,
    )
    minibatch_no: Mapped[int] = mapped_column(Integer, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)  # failure | success
    n_items: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    prompt_system: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_user: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # [{item_key, span_index, field, before, after, stage}] — what the cascade cut.
    truncation: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    chars_before: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chars_after: Mapped[int | None] = mapped_column(Integer, nullable=True)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    step: Mapped["OptimizationStep"] = relationship(back_populates="minibatches")


class OptimizationStageCall(Base):
    """Everything the optimizer was asked *after* the analysts: merge, then rank.

    A step's edits are not the edits any one analyst proposed. The per-minibatch
    patches are merged hierarchically — failures together, successes together,
    then the two groups combined with failures taking priority — and if the pool
    is still over the learning rate, a ranking call chooses which survive. Three
    or more model calls, each of which can drop an edit or rewrite it.

    None of it was recorded. The page could show what each analyst asked for and
    what the skill ended up with, and nothing in between, so the commonest
    question about a disappointing step — "which stage lost my edit?" — had no
    answer on the page at all.

    Stored per call rather than as a summary because the useful artefact is the
    prompt: merge and ranking are LLM calls with the same failure modes as the
    analyst, and one that misread its input looks exactly like one that judged
    correctly unless its input is on screen.
    """

    __tablename__ = "optimization_stage_calls"
    __table_args__ = (UniqueConstraint("step_id", "seq"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    step_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("optimization_steps.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Submission order within the step, so the page can lay the stages out in the
    # order they happened without inferring it from timestamps that a thread pool
    # makes meaningless.
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    # merge_failure | merge_success | merge_final | ranking
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    # Which round of the hierarchical merge, where there was one. Null for the
    # final merge and for ranking, which happen once.
    level: Mapped[int | None] = mapped_column(Integer, nullable=True)

    prompt_system: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_user: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The answer as parsed, not as typed. The raw text buys nothing here: every
    # one of these stages is a JSON contract, and when parsing fails the patch
    # is discarded and `error` says so.
    output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    step: Mapped["OptimizationStep"] = relationship(back_populates="stage_calls")


class OptimizationSkill(Base):
    """A full snapshot of the skill directory at one step.

    Whole copies rather than stored diffs: a skill is a few kilobytes, a run is a
    handful of steps, and the diff shown on screen has to be computed against an
    arbitrary base anyway ("vs the previous accepted step" or "vs the initial
    skill"). Reconstructing a snapshot by replaying patches would also make the
    displayed diff depend on every earlier step being correct.
    """

    __tablename__ = "optimization_skills"
    __table_args__ = (UniqueConstraint("run_id", "step_no", "kind"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("optimization_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_no: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # initial | candidate
    files: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    # {path: {added, removed}} against this step's parent. Computed once here so
    # the file tree, the step row and the chart tooltip cannot disagree about the
    # same edit.
    per_file_stats: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    run: Mapped["OptimizationRun"] = relationship(back_populates="skills")


# --- Personal defaults ------------------------------------------------------


class UserSettings(Base):
    """One developer's own defaults for the forms in this product.

    Keyed by subject, and by the *normalised* subject — `auth.normalize_subject`,
    the same casefold `eval_set_roles` is written with. Without that, `Alice` and
    `alice` would be two people with two sets of defaults, one of which looks
    empty for no reason the user can see.

    `values` and `secrets` are separate columns for the reason `runs` splits
    them: no response model reads `secrets`, so "credentials never leave the
    server" is a property of the schema rather than of somebody remembering.
    Unlike `runs.secrets`, these are encrypted at rest as well — see
    `services/user_secrets.py` for why a saved default is a different risk from
    one run's key.

    **`values` is keyed on presence, never on truthiness.** A key that is absent
    means "no opinion, use the environment". A key that is present means the user
    chose that value, and `False`, `0`, `""` and `null` are all values they can
    choose. Reading this column with `or`, or with a falsiness test, silently
    turns four legitimate answers back into the deployment's default — the exact
    bug `optimizer/hyperparams.py` and `optimizer/stopping.py` were each rewritten
    to remove.
    """

    __tablename__ = "user_settings"

    subject: Mapped[str] = mapped_column(Text, primary_key=True)
    values: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # What the environment said for each overridden key at the moment it was
    # overridden. The point is the day someone edits `.env`: a user who
    # overrode that key keeps winning, silently, possibly pointing at an
    # endpoint that no longer exists. Comparing this against today's value is
    # what lets the settings page say so.
    system_at_set: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    secrets: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # Which settings this user has already been shown. "New" has to mean "you
    # have not seen this", not "you have not set this" — otherwise a first visit
    # is twenty-five badges and the signal is worthless on day one. The row is
    # created on that first visit with every current key already in here, so
    # only keys introduced afterwards are ever new.
    seen_keys: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
