"""Integration seams (§6.15 external deps).

Which implementation backs each seam is chosen per seam by the `*_IMPL` settings
(`AGENT_IMPL`, `JUDGE_IMPL`, `TRACE_IMPL`, `DIAGNOSIS_IMPL`, `SYNTHESIS_IMPL`,
`WORKSPACE_IMPL`, each
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
    OptimizerClient,
    SynthesisClient,
    TraceClient,
    WorkspaceClient,
)
from app.integrations.fake import (
    FakeAgentClient,
    FakeDiagnosisClient,
    FakeJudgeClient,
    FakeOptimizerClient,
    FakeSynthesisClient,
    FakeTraceClient,
    FakeWorkspaceClient,
)


@dataclass
class Seams:
    """The clients one run — or one playground attempt — executes against.

    `synthesis` and `workspace` have defaults because an eval run needs neither:
    only the playground drafts an expected process or reads the agent's config
    and skill files (§10). Callers that predate
    it keep working, and a test can still build a Seams with just the seam it
    cares about.
    """

    agent: AgentClient
    judge: JudgeClient
    trace: TraceClient
    diagnosis: DiagnosisClient
    synthesis: SynthesisClient | None = None
    workspace: WorkspaceClient | None = None
    # Only an optimization run uses this one, and it is the only seam here that
    # is synchronous — see `base.OptimizerClient`. Defaulted like the two above
    # so every existing caller keeps working unchanged.
    optimizer: OptimizerClient | None = None


def _get(config: dict | None, key: str):
    """A config value, treating blank/missing as "fall back to the environment"."""
    if not config:
        return None
    value = config.get(key)
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _workspace_auth(config: dict | None, secrets: dict | None, skills_url: str) -> dict:
    """The credential arguments for the skills client: the chat endpoint's, or none.

    Split out so the rule has one name and one test. Returning a dict of kwargs
    rather than a key keeps the "send nothing" case free of an explicit
    `api_key=None` at the call site, which is the case that must stay obviously
    inert.
    """
    from app.integrations.real.agent_auth import same_origin

    api_key = _get(secrets, "agent_api_key") or settings.agent_api_key
    chat_url = _get(config, "agent_chat_url") or settings.agent_chat_url
    if not api_key or not same_origin(chat_url, skills_url):
        return {}
    return {
        "api_key": api_key,
        "auth_header": _get(config, "agent_auth_header") or settings.agent_auth_header,
    }


def build_seams(
    config: dict | None = None,
    secrets: dict | None = None,
    include_workspace: bool = False,
    include_optimizer: bool = False,
) -> Seams:
    """Build the clients for one run. Blank config falls back to the environment.

    `include_workspace` is opt-in because a misconfigured workspace seam must not
    be able to break the eval path: nothing in a run reads the agent's config or
    skills. Only the playground, the wizard and the pre-flights ask for it.

    **A missing skills URL is not a misconfiguration.** With
    `WORKSPACE_IMPL=real` and no `agent_skills_url`, `workspace` comes back
    `None` rather than raising. That is the whole of the entry-level tier: an
    agent with only a chat endpoint can be evaluated, and the features that need
    the file listing — the playground's editor, the wizard's skill check,
    optimization — are the ones that have to say so. Callers must handle `None`;
    the ones that cannot work without it turn it into a sentence naming the
    missing endpoint.
    """
    agent: AgentClient
    if settings.agent_impl == "real":
        from app.integrations.real.agent import HttpAgentClient

        agent = HttpAgentClient(
            chat_url=_get(config, "agent_chat_url"),
            timeout_s=_get(config, "agent_timeout_s"),
            # A credential, so it comes from `secrets` and never from `config`
            # — the two are stored in different columns and only one of them is
            # allowed near a response model. The header name is not a secret.
            api_key=_get(secrets, "agent_api_key"),
            auth_header=_get(config, "agent_auth_header"),
        )
    else:
        agent = FakeAgentClient()

    judge: JudgeClient
    diagnosis: DiagnosisClient
    llm = None
    if (
        settings.judge_impl == "real"
        or settings.diagnosis_impl == "real"
        or settings.synthesis_impl == "real"
    ):
        from app.integrations.real.llm import get_client_for

        llm = get_client_for(
            base_url=_get(config, "llm_base_url"),
            api_key=_get(secrets, "llm_api_key"),
        )

    if settings.judge_impl == "real":
        from app.integrations.real.judge import LlmJudgeClient

        # The prompt travels in the run config like everything else here, but it
        # is put there by `trigger_run` from the eval set — never by the caller
        # (services/judge_prompt).
        judge = LlmJudgeClient(
            model=_get(config, "judge_model"),
            llm=llm,
            system_prompt=_get(config, "judge_system_prompt"),
            user_template=_get(config, "judge_user_prompt"),
        )
    else:
        judge = FakeJudgeClient()

    if settings.diagnosis_impl == "real":
        from app.integrations.real.diagnosis import LlmDiagnosisClient

        diagnosis = LlmDiagnosisClient(model=_get(config, "diagnosis_model"), llm=llm)
    else:
        diagnosis = FakeDiagnosisClient()

    synthesis: SynthesisClient
    if settings.synthesis_impl == "real":
        from app.integrations.real.synthesis import LlmSynthesisClient

        synthesis = LlmSynthesisClient(model=_get(config, "synthesis_model"), llm=llm)
    else:
        synthesis = FakeSynthesisClient()

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

            skills_url = _get(config, "agent_skills_url") or settings.agent_skills_url
            # No URL, no client — and no exception. See the docstring: this is
            # the supported "chat endpoint only" configuration, not a mistake to
            # report here. Reporting it here would put the sentence in front of
            # someone running an eval, which does not need it.
            if skills_url:
                workspace = HttpWorkspaceClient(
                    skills_url=skills_url,
                    timeout_s=_get(config, "agent_timeout_s"),
                    # The chat endpoint's credential reaches the skills endpoint
                    # only when the two are the same server. There is one field
                    # because there is one agent; there is this test because
                    # "one agent" is an assumption the form cannot enforce, and
                    # a credential typed against one host must not follow the
                    # other field wherever it is pointed. See
                    # `services/user_secrets.py` on endpoint binding.
                    **_workspace_auth(config, secrets, skills_url),
                )
        else:
            workspace = FakeWorkspaceClient()

    # Only an optimization run needs this, so like `include_workspace` it is
    # opt-in: a deployment with OPTIMIZER_IMPL=real and no LLM base URL must not
    # break the eval path, which never touches it.
    optimizer: OptimizerClient | None = None
    if include_optimizer:
        if settings.optimizer_impl == "real":
            from app.integrations.real.optimizer import LlmOptimizerClient

            optimizer = LlmOptimizerClient(
                model=_get(config, "optimizer_model"),
                base_url=_get(config, "llm_base_url"),
                api_key=_get(secrets, "llm_api_key"),
            )
        else:
            optimizer = FakeOptimizerClient()

    return Seams(
        agent=agent, judge=judge, trace=trace, diagnosis=diagnosis,
        synthesis=synthesis, workspace=workspace, optimizer=optimizer,
    )


__all__ = ["Seams", "build_seams"]
