"""The five integration seams as Protocols + shared data types (§6.15, §10.2).

A real implementation swaps in behind the SAME interface — the orchestrator and
routers depend only on these Protocols, never on a concrete module.

Seams:
    AgentClient.call(question, correlation_id, user_id, tags, skill_override) -> AgentResponse
    JudgeClient.judge(question, response, ground_truth)               -> Verdict
    TraceClient.fetch_trace(correlation_id)                           -> Trace | NotReady
    DiagnosisClient.diagnose(trace, ground_truth_reasoning, verdict)  -> dict (§6.9 JSON)
    SkillClient.list_skills() / .get_skill(name)                      -> the agent's skills
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


@dataclass
class SkillSummary:
    """One entry of the agent's skill catalogue (§10.2)."""
    name: str
    description: str | None = None


@dataclass
class Skill:
    """A skill's full text, as the agent server currently holds it."""
    name: str
    content: str
    description: str | None = None


@dataclass
class SkillOverride:
    """A candidate skill to use for ONE agent call instead of the stored one.

    The playground's whole point (§4.7 / §6.5): try an edited skill without
    writing it back to the agent server. `name` travels with the content because
    the agent has to know *which* skill this replaces — a nameless blob of text
    tells it nothing about where to substitute it.
    """
    name: str
    content: str


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
    # `skill_override` is keyword-with-a-default on purpose: an eval run never
    # sends one, so the run path is untouched by the playground existing.
    async def call(
        self, question: str, correlation_id: str, user_id: str,
        tags: list[str] | None = None,
        skill_override: "SkillOverride | None" = None,
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

    # `judge_verdict` is optional because the playground allows an expected
    # reasoning process with no expected answer (§10.4): there is a flow to
    # compare the trace against, but nothing was graded. An eval run always has
    # a verdict — it only diagnoses questions the judge marked incorrect.
    async def diagnose(
        self, trace: Trace, ground_truth_reasoning: str,
        judge_verdict: Verdict | None,
    ) -> dict: ...


@runtime_checkable
class SkillClient(Protocol):
    """Read the agent's skill catalogue, so the playground can edit from the
    real starting point rather than from a blank textarea (§10.2).

    Read-only by design: writing an optimized skill back to the agent server
    needs versioning and rollback (§4.9) and belongs to Stage 3.
    """

    async def list_skills(self) -> list[SkillSummary]: ...

    async def get_skill(self, name: str) -> Skill: ...
