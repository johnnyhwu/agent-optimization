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
    status: Mapped[str] = mapped_column(Text, nullable=False)  # running|completed|failed
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
    status: Mapped[str] = mapped_column(Text, nullable=False)  # pending|done|failed
    # Why status='failed' (agent error, judge error, timeout, ...).
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trace_ready: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
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
