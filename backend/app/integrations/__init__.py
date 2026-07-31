"""Integration seams (§6.15 external deps).

Which implementation backs each seam is chosen per seam by the `*_IMPL` settings
(`AGENT_IMPL`, `JUDGE_IMPL`, `TRACE_IMPL`, `DIAGNOSIS_IMPL`, `WORKSPACE_IMPL`, each
`fake` or `real`), so the real integrations can be brought up one at a time — a
real agent while the judge is still fake, and so on. Everything downstream
depends on the Protocols in `base.py` and never on a concrete class.

*Which endpoint* a real seam talks to is per run, not per process: the run
carries its own base URLs, models and timeouts (chosen in the UI at trigger
time) and `build_seams` turns that into a set of clients. Two runs executing
concurrently therefore get their own clients rather than racing over shared
state — `trigger_run` spawns background tasks with no lock, so a mutable global
would be a genuine hazard here.

The `*_IMPL` switches stay the master switch: a blank run config falls back to
the environment, so `build_seams()` with no arguments reproduces the
environment-only behaviour and the seeded fake demo needs no configuration at
all.

The real implementations are imported lazily: a fake-only deployment must not
need an LLM endpoint or Langfuse to be configured.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.config import settings
from app.integrations.base import (
    AgentClient,
    DiagnosisClient,
    JudgeClient,
    TraceClient,
    WorkspaceClient,
)
from app.integrations.fake import (
    FakeAgentClient,
    FakeDiagnosisClient,
    FakeJudgeClient,
    FakeTraceClient,
    FakeWorkspaceClient,
)


@dataclass
class Seams:
    """The clients one run — or one playground attempt — executes against.

    `workspace` has a default because an eval run never reads the agent's
    config or skill files: only the playground does (§10). Callers that predate
    it keep working, and a test can still build a Seams with just the seam it
    cares about.
    """

    agent: AgentClient
    judge: JudgeClient
    trace: TraceClient
    diagnosis: DiagnosisClient
    workspace: WorkspaceClient | None = None


def _get(config: dict | None, key: str):
    """A config value, treating blank/missing as "fall back to the environment"."""
    if not config:
        return None
    value = config.get(key)
    if isinstance(value, str) and not value.strip():
        return None
    return value


def build_seams(
    config: dict | None = None,
    secrets: dict | None = None,
    include_workspace: bool = False,
) -> Seams:
    """Build the clients for one run. Blank config falls back to the environment.

    `include_workspace` is opt-in because a misconfigured workspace seam must not
    be able to break the eval path: `WORKSPACE_IMPL=real` with no agent base URL
    raises, and nothing in a run reads the agent's config or skills. Only the
    playground asks for it, and only the playground's workspace endpoints answer
    for it.
    """
    agent: AgentClient
    if settings.agent_impl == "real":
        from app.integrations.real.agent import HttpAgentClient

        agent = HttpAgentClient(
            base_url=_get(config, "agent_base_url"),
            timeout_s=_get(config, "agent_timeout_s"),
        )
    else:
        agent = FakeAgentClient()

    judge: JudgeClient
    diagnosis: DiagnosisClient
    llm = None
    if settings.judge_impl == "real" or settings.diagnosis_impl == "real":
        from app.integrations.real.llm import get_client_for

        llm = get_client_for(
            base_url=_get(config, "llm_base_url"),
            api_key=_get(secrets, "llm_api_key"),
        )

    if settings.judge_impl == "real":
        from app.integrations.real.judge import LlmJudgeClient

        judge = LlmJudgeClient(model=_get(config, "judge_model"), llm=llm)
    else:
        judge = FakeJudgeClient()

    if settings.diagnosis_impl == "real":
        from app.integrations.real.diagnosis import LlmDiagnosisClient

        diagnosis = LlmDiagnosisClient(model=_get(config, "diagnosis_model"), llm=llm)
    else:
        diagnosis = FakeDiagnosisClient()

    trace: TraceClient
    if settings.trace_impl == "real":
        from app.integrations.real.langfuse import LangfuseTraceClient

        trace = LangfuseTraceClient(
            host=_get(config, "langfuse_host"),
            public_key=_get(config, "langfuse_public_key"),
            secret_key=_get(secrets, "langfuse_secret_key"),
            timeout_s=_get(config, "langfuse_timeout_s"),
        )
    else:
        trace = FakeTraceClient()

    workspace: WorkspaceClient | None = None
    if include_workspace:
        if settings.workspace_impl == "real":
            from app.integrations.real.workspace import HttpWorkspaceClient

            workspace = HttpWorkspaceClient(
                base_url=_get(config, "agent_base_url"),
                timeout_s=_get(config, "agent_timeout_s"),
            )
        else:
            workspace = FakeWorkspaceClient()

    return Seams(
        agent=agent, judge=judge, trace=trace, diagnosis=diagnosis,
        workspace=workspace,
    )


__all__ = ["Seams", "build_seams"]
