"""Integration seams (§6.15 external deps).

Import the concrete clients from here. Which implementation backs each seam is
chosen per seam by settings (`AGENT_IMPL`, `JUDGE_IMPL`, `TRACE_IMPL`,
`DIAGNOSIS_IMPL`, each `fake` or `real`), so the real integrations can be brought
up one at a time — a real agent while the judge is still fake, and so on.
Everything downstream imports the four names below and never a concrete class.

The real implementations are imported lazily: a fake-only deployment must not
need an LLM endpoint or Langfuse to be configured.
"""
from __future__ import annotations

from app.config import settings
from app.integrations.base import (
    AgentClient,
    DiagnosisClient,
    JudgeClient,
    TraceClient,
)
from app.integrations.fake import (
    FakeAgentClient,
    FakeDiagnosisClient,
    FakeJudgeClient,
    FakeTraceClient,
)


def _build_agent_client() -> AgentClient:
    if settings.agent_impl == "real":
        from app.integrations.real.agent import HttpAgentClient

        return HttpAgentClient()
    return FakeAgentClient()


def _build_judge_client() -> JudgeClient:
    if settings.judge_impl == "real":
        from app.integrations.real.judge import LlmJudgeClient

        return LlmJudgeClient()
    return FakeJudgeClient()


def _build_trace_client() -> TraceClient:
    if settings.trace_impl == "real":
        from app.integrations.real.langfuse import LangfuseTraceClient

        return LangfuseTraceClient()
    return FakeTraceClient()


def _build_diagnosis_client() -> DiagnosisClient:
    if settings.diagnosis_impl == "real":
        from app.integrations.real.diagnosis import LlmDiagnosisClient

        return LlmDiagnosisClient()
    return FakeDiagnosisClient()


# The active clients used by the orchestrator / routers.
agent_client: AgentClient = _build_agent_client()
judge_client: JudgeClient = _build_judge_client()
trace_client: TraceClient = _build_trace_client()
diagnosis_client: DiagnosisClient = _build_diagnosis_client()

__all__ = ["agent_client", "judge_client", "trace_client", "diagnosis_client"]
