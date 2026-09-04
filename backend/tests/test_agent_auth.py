"""Sending a credential to the agent server — and, mostly, not sending one.

Authentication is not part of the agent server contract. The platform gained the
ability to send a credential so that a team whose agent sits behind a gateway
could connect at all; every team whose agent asks for nothing must be completely
unaffected, and the first test in this file is the one that says so.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from app.integrations import build_seams
from app.integrations.real.agent import HttpAgentClient
from app.integrations.real.agent_auth import auth_headers, redact, same_origin
from app.integrations.real.workspace import HttpWorkspaceClient

CHAT_URL = "https://agent.test/v1/chat/completions"
SKILLS_URL = "https://agent.test/skills"


def completion(text: str) -> dict:
    return {"choices": [{"index": 0, "message": {"role": "assistant", "content": text}}]}


# --- The header ---------------------------------------------------------------

def test_no_key_means_no_header_at_all():
    """Not an empty `Authorization`, which some gateways reject more
    confusingly than no credential does."""
    assert auth_headers("", "") == {}
    assert auth_headers(None) == {}
    assert auth_headers("   ", "X-Api-Key") == {}


def test_a_key_alone_is_a_bearer_token():
    assert auth_headers("abc123") == {"Authorization": "Bearer abc123"}


def test_a_named_header_carries_the_key_verbatim():
    """`X-Api-Key: Bearer abc` is what a gateway asking for `X-Api-Key` would
    reject — the prefix belongs to Authorization and to nothing else."""
    assert auth_headers("abc123", "X-Api-Key") == {"X-Api-Key": "abc123"}


def test_a_value_that_already_says_bearer_is_not_doubled():
    # Someone pasting from a curl command has given a complete header value.
    assert auth_headers("Bearer abc123") == {"Authorization": "Bearer abc123"}
    assert auth_headers("bearer abc123") == {"Authorization": "bearer abc123"}


def test_the_default_header_is_recognised_however_it_is_typed():
    assert auth_headers("abc", "authorization") == {"authorization": "Bearer abc"}


# --- Where it may go ----------------------------------------------------------

@pytest.mark.parametrize(
    "a,b,expected",
    [
        ("https://agent.test/v1/chat/completions", "https://agent.test/skills", True),
        # The default port is the port.
        ("https://agent.test:443/chat", "https://agent.test/skills", True),
        ("http://agent.test:8080/chat", "http://agent.test:8080/skills", True),
        # A different port is a different service, which may well be somebody
        # else's.
        ("http://agent.test:8080/chat", "http://agent.test:9090/skills", False),
        ("https://agent.test/chat", "http://agent.test/skills", False),
        # The case a string prefix comparison would get wrong, and the reason
        # this is parsed rather than compared.
        ("https://agent.test/chat", "https://agent.test.evil.example/skills", False),
        ("https://agent.test/chat", "", False),
        ("", "", False),
        ("agent.test/chat", "agent.test/skills", False),  # no scheme, no match
    ],
)
def test_same_origin(a, b, expected):
    assert same_origin(a, b) is expected


def test_redact_removes_the_key_and_the_header_value():
    assert redact("sent Authorization: Bearer sk-42", "sk-42") == (
        "sent Authorization: <redacted>"
    )
    assert redact("key was sk-42", "sk-42") == "key was <redacted>"
    assert redact("nothing here", "sk-42") == "nothing here"
    assert redact("nothing here", "") == "nothing here"


# --- The clients --------------------------------------------------------------

@pytest.fixture
def real(configure):
    with configure(
        agent_impl="real", workspace_impl="real",
        agent_chat_url=CHAT_URL, agent_skills_url=SKILLS_URL,
        agent_timeout_s=30.0, agent_probe_timeout_s=5.0,
        agent_api_key="", agent_auth_header="",
    ):
        yield


@respx.mock
async def test_an_unauthenticated_agent_sees_exactly_what_it_saw_before(real):
    """The whole point of this feature being optional.

    A deployment that never fills in a key must produce the request it produced
    before authentication existed — one header, `Content-Type`. If this test
    ever needs updating, the change it is reporting is that every existing agent
    server started receiving something new.
    """
    route = respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=completion("ok")))
    await HttpAgentClient(chat_url=CHAT_URL).call("q", "cid", "user")

    sent = route.calls[0].request.headers
    assert "authorization" not in sent
    assert sent["content-type"] == "application/json"


@respx.mock
async def test_a_configured_key_reaches_the_chat_endpoint(real):
    route = respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=completion("ok")))
    await HttpAgentClient(chat_url=CHAT_URL, api_key="sk-42").call("q", "cid", "user")

    assert route.calls[0].request.headers["authorization"] == "Bearer sk-42"


@respx.mock
async def test_the_agents_own_words_never_carry_our_key_back_out(real):
    """A gateway echoing the request headers into its error body is a real
    thing, and that body is quoted into run records and onto a screen."""
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(
            400, json={"error": {"message": "bad credential Bearer sk-42"}}
        )
    )
    answer = await HttpAgentClient(chat_url=CHAT_URL, api_key="sk-42").call("q", "cid", "u")

    assert answer.failed
    assert "sk-42" not in answer.error
    assert "<redacted>" in answer.error


@respx.mock
async def test_the_skills_client_sends_what_it_was_given(real):
    route = respx.get(SKILLS_URL).mock(
        return_value=httpx.Response(200, json={"version": "v1", "skills": {}})
    )
    await HttpWorkspaceClient(skills_url=SKILLS_URL, api_key="sk-42").get_workspace()

    assert route.calls[0].request.headers["authorization"] == "Bearer sk-42"


# --- The rule that decides ----------------------------------------------------

def test_the_seam_sends_the_chat_credential_to_a_skills_endpoint_on_the_same_host(real):
    seams = build_seams(
        {"agent_chat_url": CHAT_URL, "agent_skills_url": SKILLS_URL},
        {"agent_api_key": "sk-42"},
        include_workspace=True,
    )
    assert seams.workspace.api_key == "sk-42"


def test_the_seam_withholds_it_from_a_skills_endpoint_somewhere_else(real):
    """The binding `services/user_secrets.py` describes: a credential goes to
    the address its owner was looking at, and a second URL field pointed
    elsewhere is elsewhere."""
    seams = build_seams(
        {"agent_chat_url": CHAT_URL, "agent_skills_url": "https://elsewhere.test/skills"},
        {"agent_api_key": "sk-42"},
        include_workspace=True,
    )
    assert seams.workspace.api_key == ""


def test_the_header_name_travels_with_the_key(real):
    seams = build_seams(
        {
            "agent_chat_url": CHAT_URL,
            "agent_skills_url": SKILLS_URL,
            "agent_auth_header": "X-Api-Key",
        },
        {"agent_api_key": "sk-42"},
        include_workspace=True,
    )
    assert seams.agent._headers()["X-Api-Key"] == "sk-42"
    assert seams.workspace.auth_header == "X-Api-Key"
