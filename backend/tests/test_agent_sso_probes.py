"""The other half of SSO forwarding: the calls that read the agent *inside* a
request.

`test_agent_sso_runs.py` covers the background half — a run whose credential has
to outlive the request that started it, which is what the registry exists for.
This file covers everything else that talks to the agent server: the connection
probes, the conformance checklist, the wizard's skill check, the optimization
run's skill snapshot, and the playground's two workspace reads. Nine endpoints
in all.

**Why this needs its own file, and why the first test is a list.** These sites
have no registry entry to fall back on and no shared helper they are forced
through — each one assembles its own `api_key` argument. Under
`AGENT_SSO_ENABLED` with no deployment-wide key there is no other credential in
play, so a site that forgets the caller's token does not degrade: a healthy
agent answers 401 and the screen reports a broken agent server. Four of the nine
were written without it, and nothing went red — the wizard simply could not get
past step 3.

So the guard is structural as well as behavioural. `current_token` is the
dependency that carries the caller's own bearer token, and the list below is
every route allowed to want it and every route required to. A tenth endpoint
that reads the agent and forgets it fails the first test rather than a
developer's afternoon.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from app import agent_sso
from app.auth import current_token
from app.main import app
from app.routers import agent as agent_router
from app.routers import optimization as optimization_router
from app.routers import playground as playground_router
from app.routers.playground import WorkspaceReadIn
from app.schemas import ChatProbeIn, RunConfig, SkillCheckIn, SkillsProbeIn

CHAT_URL = "https://agent.test/v1/chat/completions"
SKILLS_URL = "https://agent.test/skills"
CALLER_TOKEN = "caller-access-token"


@pytest.fixture
def sso(configure):
    """A deployment that forwards identity and has no key of its own.

    `agent_api_key=""` is the case that matters: with a deployment-wide key the
    sites that forget the caller's token still send *something* and the bug
    hides behind a working request.
    """
    with configure(
        auth_mode="keycloak", agent_sso_enabled=True,
        agent_impl="real", workspace_impl="real",
        agent_chat_url=CHAT_URL, agent_skills_url=SKILLS_URL,
        agent_api_key="", agent_auth_header="Authorization",
        agent_timeout_s=30.0, agent_probe_timeout_s=5.0,
    ) as settings:
        yield settings


def skills_response() -> httpx.Response:
    return httpx.Response(
        200, json={"version": "v1", "skills": {"billing/SKILL.md": "# Billing\n"}}
    )


def completion(text: str = "ok") -> httpx.Response:
    return httpx.Response(200, json={
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                     "finish_reason": "stop"}]
    })


def sent_auth(route) -> str | None:
    """The Authorization header the platform actually put on the wire."""
    assert route.called, "the agent was never called"
    return route.calls.last.request.headers.get("Authorization")


# --- The structural guard ---------------------------------------------------

# Every route that reads the agent server while the caller is still on the other
# end of the socket. Written out rather than derived, because the whole failure
# mode is a site nobody remembered.
FORWARDING_ROUTES = {
    ("POST", "/agent/skills"),
    ("POST", "/agent/chat-probe"),
    ("POST", "/agent/conformance"),
    ("POST", "/eval-sets/{eval_set_id}/runs"),
    ("POST", "/optimization/skill-check"),
    ("POST", "/optimization/runs"),
    ("POST", "/playground/workspace"),
    ("POST", "/playground/workspace/version"),
    ("POST", "/playground/attempts"),
}


def _routes_depending_on_current_token() -> set[tuple[str, str]]:
    found = set()
    for route in app.routes:
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        if any(d.call is current_token for d in dependant.dependencies):
            for method in getattr(route, "methods", ()):
                found.add((method, route.path))
    return found


def test_every_agent_reading_endpoint_takes_the_caller_s_token():
    """The test that would have caught four missed sites.

    A route in the list without the dependency cannot forward identity at all —
    it has no token to forward — so under SSO it goes out anonymous. A route
    with the dependency and not in the list is asking for a credential it has no
    stated use for, which is the direction worth catching early.
    """
    assert _routes_depending_on_current_token() == FORWARDING_ROUTES


# --- The rule, on its own ---------------------------------------------------

def test_a_typed_key_wins_over_the_signed_in_user(sso):
    """The escape hatch. The advanced fields exist for an agent that wants its
    own key, and a key typed there must be the one that is sent."""
    assert agent_sso.probe_key("typed-key", CALLER_TOKEN) == "typed-key"


def test_the_signed_in_user_is_used_when_nothing_was_typed(sso):
    assert agent_sso.probe_key("", CALLER_TOKEN) == CALLER_TOKEN
    assert agent_sso.probe_key(None, CALLER_TOKEN) == CALLER_TOKEN


def test_blank_falls_through_to_the_deployment_s_own_key(sso):
    """`""` rather than the deployment key itself: the seam applies that
    fallback, and duplicating it here would be a second place for the
    precedence to be written down and disagreed with."""
    assert agent_sso.probe_key("", None) == ""


def test_the_caller_s_token_is_ignored_while_the_switch_is_off(configure):
    """The whole feature is one flag, and a deployment that has not turned it on
    must send exactly what it always sent."""
    with configure(auth_mode="keycloak", agent_sso_enabled=False):
        assert agent_sso.probe_key("", CALLER_TOKEN) == ""
    with configure(auth_mode="fake", agent_sso_enabled=True):
        assert agent_sso.probe_key("", CALLER_TOKEN) == ""


def test_whitespace_is_not_a_typed_key(sso):
    """A field the developer opened, tabbed through and left is not a choice."""
    assert agent_sso.probe_key("   ", CALLER_TOKEN) == CALLER_TOKEN


def test_probe_kwargs_is_the_same_rule_in_build_seams_shape(sso):
    """Two shapes, one rule. `probe_kwargs` exists for the callers that pass a
    whole secrets dict — writing the token into that dict would put it one
    `model_dump()` away from a `secrets` column."""
    kwargs = agent_sso.probe_kwargs(CALLER_TOKEN)
    assert set(kwargs) == {"agent_credential"}
    assert agent_sso.probe_kwargs(None) == {}


async def test_probe_kwargs_resolves_to_the_caller_s_token(sso):
    credential = agent_sso.probe_kwargs(CALLER_TOKEN)["agent_credential"]
    assert await credential.value() == CALLER_TOKEN


# --- The nine sites, on the wire --------------------------------------------

@respx.mock
async def test_the_skills_probe_reads_as_the_signed_in_user(sso):
    route = respx.get(SKILLS_URL).mock(return_value=skills_response())
    out = await agent_router.agent_skills(
        SkillsProbeIn(config=RunConfig(agent_skills_url=SKILLS_URL,
                                       agent_chat_url=CHAT_URL)),
        subject="alice", caller_token=CALLER_TOKEN,
    )
    assert out.check.ok is True
    assert sent_auth(route) == f"Bearer {CALLER_TOKEN}"


@respx.mock
async def test_the_chat_probe_asks_as_the_signed_in_user(sso):
    route = respx.post(CHAT_URL).mock(return_value=completion())
    await agent_router.chat_probe(
        ChatProbeIn(config=RunConfig(agent_chat_url=CHAT_URL),
                    with_override=False, with_trace=False),
        subject="alice", caller_token=CALLER_TOKEN,
    )
    assert sent_auth(route) == f"Bearer {CALLER_TOKEN}"


@respx.mock
async def test_the_wizard_s_skill_check_reads_as_the_signed_in_user(sso):
    """Wizard step 3. Without the token this endpoint 401s against a working
    agent, and the wizard cannot be finished at all."""
    route = respx.get(SKILLS_URL).mock(return_value=skills_response())
    out = await optimization_router.skill_check(
        SkillCheckIn(skill_name="billing", agent_skills_url=SKILLS_URL,
                     agent_chat_url=CHAT_URL),
        subject="alice", caller_token=CALLER_TOKEN,
    )
    assert out.exists is True
    assert sent_auth(route) == f"Bearer {CALLER_TOKEN}"


@respx.mock
async def test_a_typed_key_still_wins_at_a_probe(sso):
    route = respx.get(SKILLS_URL).mock(return_value=skills_response())
    await optimization_router.skill_check(
        SkillCheckIn(skill_name="billing", agent_skills_url=SKILLS_URL,
                     agent_chat_url=CHAT_URL, agent_api_key="typed-key"),
        subject="alice", caller_token=CALLER_TOKEN,
    )
    assert sent_auth(route) == "Bearer typed-key"


@respx.mock
async def test_the_playground_workspace_read_uses_the_signed_in_user(sso):
    route = respx.get(SKILLS_URL).mock(return_value=skills_response())
    out = await playground_router.get_workspace(
        WorkspaceReadIn(agent_skills_url=SKILLS_URL, agent_chat_url=CHAT_URL),
        subject="alice", caller_token=CALLER_TOKEN,
    )
    assert out.version == "v1"
    assert sent_auth(route) == f"Bearer {CALLER_TOKEN}"


@respx.mock
async def test_the_playground_version_check_uses_the_signed_in_user(sso):
    """Asked once per question, so it fails as often as the snapshot does."""
    route = respx.get(SKILLS_URL).mock(return_value=skills_response())
    out = await playground_router.get_workspace_version(
        WorkspaceReadIn(agent_skills_url=SKILLS_URL, agent_chat_url=CHAT_URL),
        subject="alice", caller_token=CALLER_TOKEN,
    )
    assert out.version == "v1"
    assert sent_auth(route) == f"Bearer {CALLER_TOKEN}"


# --- What the switch being off has to look like -----------------------------

@respx.mock
async def test_nothing_is_sent_when_the_deployment_does_not_forward_identity(configure):
    """Byte-for-byte the request an untouched deployment makes. A caller's token
    arriving at a backend that was not asked to forward it is not a reason to
    start sending credentials to somebody else's agent server."""
    with configure(
        auth_mode="keycloak", agent_sso_enabled=False,
        agent_impl="real", workspace_impl="real",
        agent_chat_url=CHAT_URL, agent_skills_url=SKILLS_URL,
        agent_api_key="", agent_auth_header="Authorization",
        agent_probe_timeout_s=5.0,
    ):
        route = respx.get(SKILLS_URL).mock(return_value=skills_response())
        await playground_router.get_workspace(
            WorkspaceReadIn(agent_skills_url=SKILLS_URL, agent_chat_url=CHAT_URL),
            subject="alice", caller_token=CALLER_TOKEN,
        )
    assert sent_auth(route) is None


@respx.mock
async def test_the_token_is_withheld_from_an_agent_on_another_host(sso):
    """Same rule as a typed key: a credential entered for one server does not
    travel to another. An SSO token is the caller's identity, which makes
    sending it to an unrelated host worse, not better."""
    other = "https://elsewhere.test/skills"
    route = respx.get(other).mock(return_value=skills_response())
    await playground_router.get_workspace_version(
        WorkspaceReadIn(agent_skills_url=other, agent_chat_url=CHAT_URL),
        subject="alice", caller_token=CALLER_TOKEN,
    )
    assert sent_auth(route) is None
