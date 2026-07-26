"""Pydantic request/response models."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# --- Eval sets --------------------------------------------------------------

class ShareEntry(BaseModel):
    """One access grant on an eval set (§6.16 roles)."""
    subject: str
    role: str  # 'owner' | 'viewer'


class EvalSetCreate(BaseModel):
    name: str
    description: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    # Optional access grants beyond the creator (who is always owner).
    shares: list[ShareEntry] = Field(default_factory=list)
    # JSONL upload payload (raw file text). Stage 1 = JSONL only.
    jsonl: str


class EvalSetUpdate(BaseModel):
    """Edit name/description/metadata under optimistic lock (§6.16)."""
    name: str | None = None
    description: str | None = None
    metadata: dict[str, str] | None = None
    version: int  # client-held version; mismatch -> 409


class RolesUpdate(BaseModel):
    """Replace the share list for an eval set (owner-only)."""
    shares: list[ShareEntry] = Field(default_factory=list)


class RunTrend(BaseModel):
    run_id: uuid.UUID
    pass_rate: float | None
    started_at: datetime


class EvalSetCard(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    metadata: dict
    version: int
    created_at: datetime
    updated_at: datetime
    run_count: int
    latest_pass_rate: float | None
    trend: list[float | None]  # ordered oldest->newest pass rates (sparkline)
    regressed: int
    improved: int
    my_role: str | None
    roles: list[ShareEntry]  # current share list (for the config dialog)


# --- Questions --------------------------------------------------------------

class QuestionOut(BaseModel):
    id: uuid.UUID
    question_id: str
    question: str
    ground_truth_response: str
    ground_truth_reasoning: str
    skills: list[str]
    version: int


class QuestionUpdate(BaseModel):
    """Edit question text (locked set: no add/delete). question_id is immutable."""
    question: str | None = None
    ground_truth_response: str | None = None
    ground_truth_reasoning: str | None = None
    version: int  # optimistic lock; mismatch -> 409


# --- Runs / results ---------------------------------------------------------

class RunOut(BaseModel):
    id: uuid.UUID
    eval_set_id: uuid.UUID
    triggered_by: str
    status: str
    started_at: datetime
    completed_at: datetime | None
    pass_rate: float | None
    total_count: int | None
    correct_count: int | None
    incorrect_count: int | None = None


class QuestionResultOut(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    question_pk: uuid.UUID
    question_id: str
    question: str
    correlation_id: str
    verdict: str | None
    judge_score: float | None
    judge_comment: str | None
    status: str
    trace_ready: bool
    has_analysis: bool
    is_incorrect: bool  # per the requested multi-run mode


class SpanOut(BaseModel):
    index: int
    tool_name: str
    status: str
    input: str
    output: str
    token_usage: dict
    input_truncated: bool = False
    output_truncated: bool = False


class SuspectOut(BaseModel):
    span_index: int
    confidence: str
    reason: str
    evidence: str


class AnalysisOut(BaseModel):
    overall_diagnosis: str
    caveat: str | None
    suspects: list[SuspectOut]
    generated_at: datetime
    model_used: str


class TraceView(BaseModel):
    """Middle+right column payload for one question_result."""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    trace_state: str  # 'ready' | 'generating' | 'no_trace'
    spans: list[SpanOut] = Field(default_factory=list)
    analysis: AnalysisOut | None = None
    verdict: str | None = None
    judge_comment: str | None = None
