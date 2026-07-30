"""The four integration seams as Protocols + shared data types (§6.15).

A real implementation swaps in behind the SAME interface — the orchestrator and
routers depend only on these Protocols, never on a concrete module.

Seams:
    AgentClient.call(question, correlation_id, user_id, tags)         -> AgentResponse
    JudgeClient.judge(question, response, ground_truth)               -> Verdict
    TraceClient.fetch_trace(correlation_id)                           -> Trace | NotReady
    DiagnosisClient.diagnose(trace, ground_truth_reasoning, verdict)  -> dict (§6.9 JSON)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


# --- Shared value objects ---------------------------------------------------

@dataclass
class AgentResponse:
    response: str
    correlation_id: str
    failed: bool = False  # agent timeout/error -> question status=failed
    # Why it failed, surfaced in the UI. A bare status='failed' tells the
    # developer nothing once the agent is a real service.
    error: str | None = None
    latency_ms: int | None = None


@dataclass
class Verdict:
    verdict: str  # 'correct' | 'incorrect'
    score: float
    comment: str | None = None


@dataclass
class Span:
    """One Langfuse observation, reconstructed for the UI (§2.3 / §6.9)."""
    index: int
    tool_name: str
    status: str
    input: str
    output: str
    token_usage: dict = field(default_factory=dict)  # {"input": n, "output": n, "total": n}
    # Langfuse `statusMessage`: why an observation is at ERROR level. Only ever
    # populated by the real trace client.
    status_message: str | None = None
    # The body as the trace store actually held it, when it was structured — an
    # LLM generation's `{"tools": [...], "messages": [...]}` request and the
    # assistant message it produced. The UI renders that per message instead of
    # dumping JSON; `input`/`output` above stay text because the diagnosis
    # prompt is built from them.
    input_json: object | None = None
    output_json: object | None = None


@dataclass
class Trace:
    correlation_id: str
    spans: list[Span]


class NotReady:
    """Sentinel: Langfuse ingestion hasn't landed the trace yet (§6.12)."""


NOT_READY = NotReady()


class TraceFetchError(RuntimeError):
    """The trace store could not be reached or refused the request.

    Deliberately distinct from `NotReady`: "your Langfuse host is wrong" and
    "ingestion hasn't landed yet" produce the same empty result otherwise, and
    collapsing them is what makes a misconfigured deployment look like a trace
    that is perpetually seconds away.
    """


# --- Protocols (the swappable seams) ----------------------------------------

@runtime_checkable
class AgentClient(Protocol):
    # `user_id` is the subject who triggered the run; `tags` lets the caller
    # attach labels (e.g. the eval set name) to the agent's Langfuse metadata.
    async def call(
        self, question: str, correlation_id: str, user_id: str,
        tags: list[str] | None = None,
    ) -> AgentResponse: ...


@runtime_checkable
class JudgeClient(Protocol):
    # `question` is part of the contract because a real LLM judge needs the
    # question itself to grade an answer against the ground truth.
    async def judge(self, question: str, response: str, ground_truth: str) -> Verdict: ...


@runtime_checkable
class TraceClient(Protocol):
    async def fetch_trace(self, correlation_id: str) -> "Trace | NotReady": ...


@runtime_checkable
class DiagnosisClient(Protocol):
    # `model_name` is stored on span_analyses.model_used, so every implementation
    # exposes which model produced a diagnosis.
    model_name: str

    async def diagnose(
        self, trace: Trace, ground_truth_reasoning: str, judge_verdict: Verdict
    ) -> dict: ...
