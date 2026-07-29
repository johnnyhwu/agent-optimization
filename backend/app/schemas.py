"""Pydantic request/response models."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

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
    # Questions, always serialized as JSONL. A CSV upload is parsed and converted
    # to JSONL in the browser (§9.1), so the wire contract stays JSONL-only.
    jsonl: str
    # Which format the developer actually uploaded — recorded on the eval set for
    # provenance (§6.14 `source_format`). The payload above is JSONL either way.
    source_format: Literal["csv", "jsonl"] = "jsonl"


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

class RunConfig(BaseModel):
    """The non-secret settings a run is triggered with (§9.2 seams).

    Every field is optional: a blank value means "use the environment", which is
    what keeps the seeded fake demo runnable from an empty form. Defaults are
    served to the UI by GET /run-config/defaults rather than baked in here, so
    the form and the fallback always agree.
    """

    agent_base_url: str = ""
    agent_timeout_s: float | None = None
    langfuse_host: str = ""
    langfuse_public_key: str = ""
    langfuse_timeout_s: float | None = None
    llm_base_url: str = ""
    judge_model: str = ""
    diagnosis_model: str = ""
    # How many questions are sent to the agent at once.
    concurrency: int | None = Field(default=None, ge=1)


class RunSecrets(BaseModel):
    """Credentials for one run. Inbound only — no response model carries these."""

    langfuse_secret_key: str = ""
    llm_api_key: str = ""


class RunCreate(BaseModel):
    """Body of POST /eval-sets/{id}/runs."""

    name: str | None = None
    config: RunConfig = Field(default_factory=RunConfig)
    secrets: RunSecrets = Field(default_factory=RunSecrets)
    # Borrow the credentials of an earlier run instead of retyping them. They are
    # copied server-side and never travel to the browser; a credential is only
    # copied when its paired endpoint is unchanged (see routers/runs.py).
    reuse_secrets_from_run_id: uuid.UUID | None = None


class RunOut(BaseModel):
    id: uuid.UUID
    eval_set_id: uuid.UUID
    triggered_by: str
    name: str | None = None
    # Non-secret settings only: RunConfig has no credential fields, so this can
    # never carry one outward.
    config: RunConfig = Field(default_factory=RunConfig)
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
    agent_response: str | None = None  # what the agent actually answered
    verdict: str | None
    judge_score: float | None
    judge_comment: str | None
    status: str
    error_message: str | None = None  # why status == 'failed'
    agent_latency_ms: int | None = None
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
    status_message: str | None = None  # Langfuse statusMessage on ERROR spans


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
    # The answer under evaluation, next to what it was graded against — with a
    # real agent this is the first thing a developer wants to read.
    agent_response: str | None = None
    ground_truth_response: str | None = None
    error_message: str | None = None
