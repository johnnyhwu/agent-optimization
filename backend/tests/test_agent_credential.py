"""The credential seam: two kinds of credential, one wire format.

`integrations/real/agent_auth.Credential` exists so a forwarded SSO token — a
different string on almost every call — can reach the agent server through the
same code that carries a typed API key. The rule this file protects is that
adding the second kind changed nothing about the first: with a static
credential, every byte on the wire is what it was before SSO forwarding
existed.

The second rule is subtler and would fail silently. `_headers` and `_safe` are
built from a credential that now *moves*, and if they resolve it separately one
call can be authenticated with this minute's token and redacted against last
minute's — which writes a live credential into a run record, on the one code
path (a gateway echoing request headers into its error body) that
`agent_auth.redact` exists to cover. So both must come from a single
resolution per call, and the tests below pin that by rotating the token on
every read.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from app.integrations import _workspace_auth, build_seams
from app.integrations.real.agent import HttpAgentClient
from app.integrations.real.agent_auth import Credential, StaticCredential
from app.integrations.real.workspace import HttpWorkspaceClient

CHAT_URL = "https://agent.test/v1/chat/completions"
SKILLS_URL = "https://agent.test/skills"

ANSWER = {
    "choices": [{"message": {"role": "assistant", "content": "42"}, "finish_reason": "stop"}]
}


class RotatingCredential:
    """A credential that returns a new string every time it is asked.

    Stands in for the SSO registry without needing one: what matters to the
    clients is only that `value()` may differ between calls.
    """

    def __init__(self, prefix: str = "tok") -> None:
        self.prefix = prefix
        self.calls = 0

    async def value(self) -> str:
        self.calls += 1
        return f"{self.prefix}-{self.calls}"


# --- StaticCredential is the old behaviour, spelled out --------------------


async def test_a_static_credential_is_the_string_it_was_given():
    assert await StaticCredential("sk-42").value() == "sk-42"


async def test_a_static_credential_strips_and_treats_blank_as_no_credential():
    assert await StaticCredential("  sk-42  ").value() == "sk-42"
    assert await StaticCredential("   ").value() == ""
    assert await StaticCredential(None).value() == ""


async def test_static_credential_satisfies_the_protocol():
    assert isinstance(StaticCredential("x"), Credential)


# --- The chat client ------------------------------------------------------


async def test_a_client_with_no_credential_argument_uses_the_static_key(configure):
    """The default path. `credential=None` must be the stored key, or every
    existing caller silently stops authenticating."""
    with configure(agent_chat_url=CHAT_URL, agent_api_key="", agent_auth_header=""):
        client = HttpAgentClient(chat_url=CHAT_URL, api_key="sk-42")
    assert await client.credential.value() == "sk-42"
    assert client._headers()["Authorization"] == "Bearer sk-42"


@respx.mock
async def test_a_rotating_credential_sends_a_new_header_on_every_call(configure):
    seen: list[str] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization", ""))
        return httpx.Response(200, json=ANSWER)

    respx.post(CHAT_URL).mock(side_effect=record)
    cred = RotatingCredential()
    with configure(agent_chat_url=CHAT_URL, agent_api_key="", agent_auth_header=""):
        client = HttpAgentClient(chat_url=CHAT_URL, credential=cred)
        await client.call("q", "c1", "alice")
        await client.call("q", "c2", "alice")

    assert seen == ["Bearer tok-1", "Bearer tok-2"], (
        "each call must carry the credential resolved for that call; a repeated "
        "header means the token was captured once and will expire mid-run"
    )


@respx.mock
async def test_one_call_resolves_its_credential_exactly_once(configure):
    """Not an efficiency test. Two resolutions in one call is how the header and
    the redaction end up describing different tokens."""
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=ANSWER))
    cred = RotatingCredential()
    with configure(agent_chat_url=CHAT_URL, agent_api_key="", agent_auth_header=""):
        client = HttpAgentClient(chat_url=CHAT_URL, credential=cred)
        await client.call("q", "c1", "alice")
    assert cred.calls == 1


@respx.mock
async def test_the_token_used_for_a_call_is_redacted_from_that_calls_error(configure):
    """A gateway that echoes request headers into its error body. The quote ends
    up in a run record and on screen, so the credential has to come back out —
    and the one to remove is the one this call actually sent."""
    respx.post(CHAT_URL).mock(
        side_effect=lambda request: httpx.Response(
            400,
            json={"error": {"message": f"bad credential {request.headers['authorization']}"}},
        )
    )
    cred = RotatingCredential()
    with configure(agent_chat_url=CHAT_URL, agent_api_key="", agent_auth_header=""):
        client = HttpAgentClient(chat_url=CHAT_URL, credential=cred)
        first = await client.call("q", "c1", "alice")
        second = await client.call("q", "c2", "alice")

    assert "tok-1" not in first.error
    assert "<redacted>" in first.error
    # The second call must not be redacted against the first call's token, which
    # is the failure a single shared `self.api_key` would have produced.
    assert "tok-2" not in second.error, (
        "the second call was redacted against a stale token, so a live "
        "credential reached the run record"
    )


@respx.mock
async def test_a_blank_rotating_credential_still_sends_no_auth_header(configure):
    """The inert case has to survive the new seam: no credential means the
    request is byte-for-byte what it was before authentication existed."""
    seen: list[bool] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append("authorization" in request.headers)
        return httpx.Response(200, json=ANSWER)

    respx.post(CHAT_URL).mock(side_effect=record)
    with configure(agent_chat_url=CHAT_URL, agent_api_key="", agent_auth_header=""):
        client = HttpAgentClient(chat_url=CHAT_URL, credential=StaticCredential(""))
        await client.call("q", "c1", "alice")
    assert seen == [False]


@respx.mock
async def test_a_custom_header_name_carries_a_rotating_token_verbatim(configure):
    """`X-Api-Key: Bearer …` would be rejected by the gateway that asked for the
    header, so the no-prefix rule must hold for this credential kind too."""
    seen: list[str] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("x-api-key", ""))
        return httpx.Response(200, json=ANSWER)

    respx.post(CHAT_URL).mock(side_effect=record)
    with configure(agent_chat_url=CHAT_URL, agent_api_key="", agent_auth_header=""):
        client = HttpAgentClient(
            chat_url=CHAT_URL, auth_header="X-Api-Key", credential=RotatingCredential()
        )
        await client.call("q", "c1", "alice")
    assert seen == ["tok-1"]


# --- The workspace (skills) client ----------------------------------------


@respx.mock
async def test_the_skills_client_resolves_its_credential_per_read(configure):
    seen: list[str] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization", ""))
        return httpx.Response(200, json={"skills": {"a/SKILL.md": "x"}, "version": "v1"})

    respx.get(SKILLS_URL).mock(side_effect=record)
    with configure(agent_skills_url=SKILLS_URL, agent_timeout_s=30.0):
        client = HttpWorkspaceClient(skills_url=SKILLS_URL, credential=RotatingCredential())
        await client.get_workspace()
        await client.get_workspace()

    assert seen == ["Bearer tok-1", "Bearer tok-2"]


# --- The same-origin rule applies to both kinds ---------------------------


def test_a_static_key_reaches_a_same_origin_skills_endpoint(configure):
    with configure(agent_chat_url=CHAT_URL, agent_api_key="", agent_auth_header=""):
        kwargs = _workspace_auth(
            {"agent_chat_url": CHAT_URL}, {"agent_api_key": "sk-42"}, SKILLS_URL
        )
    assert kwargs["api_key"] == "sk-42"


def test_an_sso_credential_reaches_a_same_origin_skills_endpoint(configure):
    cred = RotatingCredential()
    with configure(agent_chat_url=CHAT_URL, agent_api_key="", agent_auth_header=""):
        kwargs = _workspace_auth({"agent_chat_url": CHAT_URL}, {}, SKILLS_URL, cred)
    assert kwargs["credential"] is cred


def test_an_sso_credential_is_withheld_from_another_host(configure):
    """The endpoint-binding rule is about the address, not about where the
    credential came from. A forwarded SSO token is still the user's, and a
    skills URL pointed elsewhere is still somewhere else."""
    with configure(agent_chat_url=CHAT_URL, agent_api_key="", agent_auth_header=""):
        kwargs = _workspace_auth(
            {"agent_chat_url": CHAT_URL},
            {},
            "https://elsewhere.test/skills",
            RotatingCredential(),
        )
    assert kwargs == {}, "an SSO token must not follow the skills URL to another host"


def test_no_credential_and_no_key_still_sends_nothing(configure):
    with configure(agent_chat_url=CHAT_URL, agent_api_key="", agent_auth_header=""):
        assert _workspace_auth({"agent_chat_url": CHAT_URL}, {}, SKILLS_URL) == {}


# --- build_seams ----------------------------------------------------------


def test_build_seams_without_a_credential_uses_the_stored_key(configure):
    """The regression guard for every caller that predates this change."""
    with configure(
        agent_impl="real", agent_chat_url=CHAT_URL, agent_api_key="", agent_auth_header=""
    ):
        seams = build_seams({"agent_chat_url": CHAT_URL}, {"agent_api_key": "sk-42"})
    assert seams.agent.api_key == "sk-42"
    assert seams.agent._headers()["Authorization"] == "Bearer sk-42"


def test_build_seams_threads_a_credential_to_both_clients(configure):
    cred = RotatingCredential()
    with configure(
        agent_impl="real",
        workspace_impl="real",
        agent_chat_url=CHAT_URL,
        agent_skills_url=SKILLS_URL,
        agent_api_key="",
        agent_auth_header="",
    ):
        seams = build_seams(
            {"agent_chat_url": CHAT_URL, "agent_skills_url": SKILLS_URL},
            {},
            include_workspace=True,
            agent_credential=cred,
        )
    assert seams.agent.credential is cred
    assert seams.workspace.credential is cred


@pytest.mark.parametrize("impl", ["fake", "real"])
def test_a_credential_never_lands_in_the_config_or_secrets_dicts(configure, impl):
    """`agent_credential` is a live object, not a value: it must stay out of the
    two dictionaries that get written to database columns."""
    config = {"agent_chat_url": CHAT_URL}
    secrets: dict = {}
    with configure(agent_impl=impl, agent_chat_url=CHAT_URL):
        build_seams(config, secrets, agent_credential=RotatingCredential())
    assert config == {"agent_chat_url": CHAT_URL}
    assert secrets == {}
