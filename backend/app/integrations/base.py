"""The four integration seams as Protocols + shared data types (§6.15).

A real implementation swaps in behind the SAME interface — the orchestrator and
routers depend only on these Protocols, never on the fake module directly.

Seams:
    AgentClient.call(question, correlation_id)              -> AgentResponse
    JudgeClient.judge(response, ground_truth)              -> Verdict
    TraceClient.fetch_trace(correlation_id)               -> Trace | NotReady
    DiagnosisClient.diagnose(trace, ground_truth, verdict) -> dict (§6.9 JSON)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


# --- Shared value objects ---------------------------------------------------

@dataclass
class AgentResponse:
    response: str
    correlation_id: str
    failed: bool = False  # simulate agent timeout/error -> question status=failed


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


@dataclass
class Trace:
    correlation_id: str
    spans: list[Span]


class NotReady:
    """Sentinel: Langfuse ingestion hasn't landed the trace yet (§6.12)."""


NOT_READY = NotReady()


# --- Protocols (the swappable seams) ----------------------------------------

@runtime_checkable
class AgentClient(Protocol):
    async def call(self, question: str, correlation_id: str) -> AgentResponse: ...


@runtime_checkable
class JudgeClient(Protocol):
    async def judge(self, response: str, ground_truth: str) -> Verdict: ...


@runtime_checkable
class TraceClient(Protocol):
    async def fetch_trace(self, correlation_id: str) -> "Trace | NotReady": ...


@runtime_checkable
class DiagnosisClient(Protocol):
    async def diagnose(
        self, trace: Trace, ground_truth_reasoning: str, judge_verdict: Verdict
    ) -> dict: ...
