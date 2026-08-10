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
