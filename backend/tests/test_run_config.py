"""Per-run eval configuration: seam selection, env fallback, and secret reuse.

Two properties carry the weight here. First, a run's config decides *which*
endpoints its seams talk to while the `*_IMPL` switches still decide fake vs
real — a blank config has to behave exactly like the environment-only setup that
existed before. Second, credentials never travel outward, and a borrowed
credential never follows the user to a different endpoint.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from app.integrations import build_seams
from app.integrations.fake import (
    FakeAgentClient,
    FakeDiagnosisClient,
    FakeJudgeClient,
    FakeTraceClient,
)
from app.integrations.real.agent import HttpAgentClient
from app.integrations.real.judge import LlmJudgeClient
from app.integrations.real.langfuse import LangfuseTraceClient
from app.routers.runs import _resolve_secrets
from app.schemas import RunConfig, RunCreate, RunOut, RunSecrets


# --- Seam selection ---------------------------------------------------------

def test_blank_config_reproduces_the_environment_only_behaviour(configure):
    # The seeded demo runs from an empty form: every seam still fake.
    with configure(agent_impl="fake", judge_impl="fake", trace_impl="fake",
                   diagnosis_impl="fake"):
        seams = build_seams()

    assert isinstance(seams.agent, FakeAgentClient)
    assert isinstance(seams.judge, FakeJudgeClient)
    assert isinstance(seams.trace, FakeTraceClient)
    assert isinstance(seams.diagnosis, FakeDiagnosisClient)


def test_impl_switches_stay_the_master_switch(configure):
    # Config naming a real endpoint does not by itself turn a fake seam real.
    with configure(agent_impl="fake"):
        seams = build_seams({"agent_base_url": "https://agent.test"})
    assert isinstance(seams.agent, FakeAgentClient)


def test_run_config_overrides_the_environment_for_a_real_seam(configure):
    with configure(agent_impl="real", agent_base_url="https://env.test",
                   agent_timeout_s=120.0):
        seams = build_seams(
            {"agent_base_url": "https://per-run.test", "agent_timeout_s": 7.0}
        )

    assert isinstance(seams.agent, HttpAgentClient)
    assert seams.agent.base_url == "https://per-run.test"
    assert seams.agent.timeout_s == 7.0


def test_blank_fields_fall_back_to_the_environment(configure):
    # A field the developer left empty must not blank out the env value.
    with configure(agent_impl="real", agent_base_url="https://env.test",
                   agent_timeout_s=99.0):
        seams = build_seams({"agent_base_url": "   ", "agent_timeout_s": None})

    assert seams.agent.base_url == "https://env.test"
    assert seams.agent.timeout_s == 99.0


def test_langfuse_credentials_come_from_config_and_secrets(configure):
    with configure(trace_impl="real", langfuse_host="https://env-lf.test",
                   langfuse_public_key="env-pub", langfuse_secret_key="env-sec"):
        seams = build_seams(
            {"langfuse_host": "https://run-lf.test", "langfuse_public_key": "pk",
             "langfuse_timeout_s": 12.0},
            {"langfuse_secret_key": "sk"},
        )

    assert isinstance(seams.trace, LangfuseTraceClient)
    assert seams.trace.host == "https://run-lf.test"
    assert (seams.trace.public_key, seams.trace.secret_key) == ("pk", "sk")
    assert seams.trace.timeout_s == 12.0


def test_judge_and_diagnosis_share_one_llm_client(configure):
    # One llm_base_url field backs both seams, so they must not open two clients.
    with configure(judge_impl="real", diagnosis_impl="real"):
        seams = build_seams(
            {"llm_base_url": "https://llm.test", "judge_model": "j",
             "diagnosis_model": "d"},
            {"llm_api_key": "k"},
        )

    assert isinstance(seams.judge, LlmJudgeClient)
    assert seams.judge.llm is seams.diagnosis.llm
    assert seams.judge.model_name == "j"
    assert seams.diagnosis.model_name == "d"


# --- Secrets never travel outward -------------------------------------------

def test_no_run_response_model_can_carry_a_credential():
    # Structural, not a review habit: the outbound models have no such fields.
    outbound = set(RunOut.model_fields) | set(RunConfig.model_fields)
    assert not [f for f in outbound if "secret" in f or "api_key" in f]
    # ...while the inbound one does.
    assert set(RunSecrets.model_fields) == {"langfuse_secret_key", "llm_api_key"}


# --- Secret reuse -----------------------------------------------------------

class _Run:
    def __init__(self, eval_set_id, config, secrets):
        self.eval_set_id = eval_set_id
        self.config = config
        self.secrets = secrets


class _Session:
    """Just enough of AsyncSession for _resolve_secrets: session.get(Run, id)."""

    def __init__(self, run=None):
        self._run = run

    async def get(self, model, pk):
        return self._run


ES = uuid.uuid4()
SOURCE_ID = uuid.uuid4()


async def test_typed_secrets_are_used_as_is():
    body = RunCreate(secrets=RunSecrets(llm_api_key="typed"))
    out = await _resolve_secrets(_Session(), ES, body, {})
    assert out == {"llm_api_key": "typed"}


async def test_reuse_copies_a_credential_when_its_endpoint_is_unchanged():
    source = _Run(ES, {"llm_base_url": "https://llm.test"}, {"llm_api_key": "borrowed"})
    body = RunCreate(reuse_secrets_from_run_id=SOURCE_ID)

    out = await _resolve_secrets(
        _Session(source), ES, body, {"llm_base_url": "https://llm.test"}
    )
    assert out["llm_api_key"] == "borrowed"


async def test_reuse_drops_a_credential_when_the_endpoint_changed():
    # Otherwise a user could borrow a stored key and point the base URL at a
    # server they control, and the backend would send the credential there.
    source = _Run(ES, {"llm_base_url": "https://llm.test"}, {"llm_api_key": "borrowed"})
    body = RunCreate(reuse_secrets_from_run_id=SOURCE_ID)

    out = await _resolve_secrets(
        _Session(source), ES, body, {"llm_base_url": "https://attacker.test"}
    )
    assert "llm_api_key" not in out


async def test_reuse_pairs_each_credential_with_its_own_endpoint():
    # Same Langfuse host, different LLM endpoint: only the Langfuse key travels.
    source = _Run(
        ES,
        {"llm_base_url": "https://llm.test", "langfuse_host": "https://lf.test"},
        {"llm_api_key": "llm-key", "langfuse_secret_key": "lf-key"},
    )
    body = RunCreate(reuse_secrets_from_run_id=SOURCE_ID)

    out = await _resolve_secrets(
        _Session(source), ES, body,
        {"llm_base_url": "https://other.test", "langfuse_host": "https://lf.test"},
    )
    assert out == {"langfuse_secret_key": "lf-key"}


async def test_typed_secret_wins_over_a_borrowed_one():
    source = _Run(ES, {"llm_base_url": "https://llm.test"}, {"llm_api_key": "borrowed"})
    body = RunCreate(
        secrets=RunSecrets(llm_api_key="typed"), reuse_secrets_from_run_id=SOURCE_ID
    )

    out = await _resolve_secrets(
        _Session(source), ES, body, {"llm_base_url": "https://llm.test"}
    )
    assert out["llm_api_key"] == "typed"


async def test_reuse_across_eval_sets_is_rejected():
    source = _Run(uuid.uuid4(), {}, {"llm_api_key": "borrowed"})
    body = RunCreate(reuse_secrets_from_run_id=SOURCE_ID)

    with pytest.raises(HTTPException) as exc:
        await _resolve_secrets(_Session(source), ES, body, {})
    assert exc.value.status_code == 404
