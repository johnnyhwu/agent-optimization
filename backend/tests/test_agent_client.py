"""HTTP agent client: request shape (message + metadata.trace_data) and the
several response shapes the agent server's /execute endpoint may return."""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.integrations.base import WorkspaceOverride
from app.integrations.real.agent import (
    AgentHttpError,
    HttpAgentClient,
    server_budget_s,
)

URL = "https://agent.test"
EXECUTE_URL = f"{URL}/execute"


@pytest.fixture
def client(configure):
    # A round timeout and margin, so the budget the server is sent (55) is
    # visibly neither of them.
    with configure(
        agent_base_url=URL, agent_timeout_s=60.0, agent_server_timeout_margin_s=5.0
    ):
        yield HttpAgentClient()


@respx.mock
async def test_request_carries_trace_data(client):
    respx.post(EXECUTE_URL).mock(return_value=httpx.Response(200, json={"content": "hi"}))
    await client.call("What is 2+2?", "corr-abc", "alice", ["eval_billing"])

    body = json.loads(respx.calls[0].request.content)
    assert body["message"] == "What is 2+2?"
    assert body["metadata"]["trace_data"] == {
        "trace_id": "corr-abc",
        "session_id": "corr-abc",
        "user_id": "alice",
        "tags": ["eval_billing"],
    }


@respx.mock
async def test_trace_id_and_session_id_are_the_same_value(client):
    respx.post(EXECUTE_URL).mock(return_value=httpx.Response(200, json={"content": "hi"}))
    await client.call("q", "corr-1", "bob")

    body = json.loads(respx.calls[0].request.content)
    assert body["metadata"]["trace_data"]["trace_id"] == "corr-1"
    assert body["metadata"]["trace_data"]["session_id"] == "corr-1"


@respx.mock
async def test_tags_default_to_empty_list(client):
    respx.post(EXECUTE_URL).mock(return_value=httpx.Response(200, json={"content": "hi"}))
    await client.call("q", "corr-1", "bob")

    body = json.loads(respx.calls[0].request.content)
    assert body["metadata"]["trace_data"]["tags"] == []


@respx.mock
async def test_no_workspace_key_without_one(client):
    """The playground's override must not leak into every other call (§10.7).

    `timeout_s` is the one key that rides along unconditionally — it states
    something true of every call. `workspace` is the opposite: only the
    playground sends one, so its existence must not add a key, or change one,
    for an eval run.
    """
    respx.post(EXECUTE_URL).mock(return_value=httpx.Response(200, json={"content": "hi"}))
    await client.call("q", "corr-1", "bob", ["eval_billing"])

    metadata = json.loads(respx.calls[0].request.content)["metadata"]
    assert "workspace" not in metadata
    assert set(metadata) == {"trace_data", "timeout_s"}


@respx.mock
async def test_request_carries_the_server_budget(client):
    """The agent server is told how long it has (§17.0 #6) — a shorter time than
    we are prepared to wait, so that it is the end that times out first."""
    respx.post(EXECUTE_URL).mock(return_value=httpx.Response(200, json={"content": "hi"}))
    await client.call("q", "corr-1", "bob")

    body = json.loads(respx.calls[0].request.content)
    assert body["metadata"]["timeout_s"] == 55.0
    # The margin is only ever subtracted from the number we send. What we
    # ourselves wait — the httpx timeout, and `pipeline.wait_for` above it — is
    # the full configured timeout, or the server would have no head start.
    assert client.timeout_s == 60.0


@respx.mock
async def test_per_run_timeout_reaches_the_server_budget(configure):
    """The timeout a developer typed into the run dialog is the one that travels."""
    with configure(
        agent_base_url=URL, agent_timeout_s=60.0, agent_server_timeout_margin_s=5.0
    ):
        respx.post(EXECUTE_URL).mock(
            return_value=httpx.Response(200, json={"content": "hi"})
        )
        await HttpAgentClient(timeout_s=600.0).call("q", "corr-1", "bob")

    assert json.loads(respx.calls[0].request.content)["metadata"]["timeout_s"] == 595.0


def test_server_budget_never_falls_below_half_the_timeout():
    """A margin wider than the timeout itself must not produce a zero or
    negative budget — a 3s question would otherwise be sent as "you have -2s"."""
    assert server_budget_s(60.0, 5.0) == 55.0
    assert server_budget_s(3.0, 5.0) == 1.5
    assert server_budget_s(10.0, 10.0) == 5.0
    # A negative margin is a misconfiguration, not licence to hand the server
    # *more* time than we will wait.
    assert server_budget_s(60.0, -5.0) == 60.0


@respx.mock
async def test_workspace_override_travels_in_metadata(client):
    respx.post(EXECUTE_URL).mock(return_value=httpx.Response(200, json={"content": "hi"}))
    await client.call(
        "q", "corr-1", "bob", ["playground"],
        workspace=WorkspaceOverride(
            config={"agents": {"defaults": {"model": "big"}}},
            skills={"billing/SKILL.md": "# Billing (edited)"},
        ),
    )

    metadata = json.loads(respx.calls[0].request.content)["metadata"]
    assert metadata["workspace"] == {
        "config": {"agents": {"defaults": {"model": "big"}}},
        "skills": {"billing/SKILL.md": "# Billing (edited)"},
    }
    # The correlation mechanism is untouched by the override riding along.
    assert metadata["trace_data"]["trace_id"] == "corr-1"
    assert metadata["trace_data"]["tags"] == ["playground"]


@respx.mock
async def test_untouched_half_of_the_override_is_omitted(client):
    """`config` absent and `config` null are not the same request.

    The agent server reads an absent half as "keep yours" and a present one as
    "use this"; sending `skills: null` for an edit that only touched the config
    would be a claim the developer never made.
    """
    respx.post(EXECUTE_URL).mock(return_value=httpx.Response(200, json={"content": "hi"}))
    await client.call(
        "q", "corr-1", "bob", ["playground"],
        workspace=WorkspaceOverride(config={"log_level": "debug"}),
    )

    assert json.loads(respx.calls[0].request.content)["metadata"]["workspace"] == {
        "config": {"log_level": "debug"}
    }


@respx.mock
async def test_empty_skills_map_is_sent_because_it_means_something(client):
    """`skills: {}` is "run with no skills", which is a legitimate experiment."""
    respx.post(EXECUTE_URL).mock(return_value=httpx.Response(200, json={"content": "hi"}))
    await client.call(
        "q", "corr-1", "bob", ["playground"], workspace=WorkspaceOverride(skills={}),
    )

    assert json.loads(respx.calls[0].request.content)["metadata"]["workspace"] == {
        "skills": {}
    }


@respx.mock
async def test_content_wrapped_response(client):
    respx.post(EXECUTE_URL).mock(return_value=httpx.Response(200, json={"content": "the answer"}))
    resp = await client.call("q", "corr", "alice")
    assert resp.failed is False
    assert resp.response == "the answer"
    assert resp.latency_ms is not None


@respx.mock
async def test_bare_json_string_response_is_also_accepted(client):
    respx.post(EXECUTE_URL).mock(return_value=httpx.Response(200, json="the answer"))
    resp = await client.call("q", "corr", "alice")
    assert resp.failed is False
    assert resp.response == "the answer"


@respx.mock
async def test_plain_text_response_is_accepted(client):
    respx.post(EXECUTE_URL).mock(
        return_value=httpx.Response(200, text="the answer", headers={"content-type": "text/plain"})
    )
    resp = await client.call("q", "corr", "alice")
    assert resp.failed is False
    assert resp.response == "the answer"


@respx.mock
async def test_content_not_a_string_is_a_failure(client):
    respx.post(EXECUTE_URL).mock(
        return_value=httpx.Response(200, json={"content": {"nested": "shape"}})
    )
    resp = await client.call("q", "corr", "alice")
    assert resp.failed is True
    assert "not a usable string" in resp.error


@respx.mock
async def test_missing_content_key_is_a_failure(client):
    respx.post(EXECUTE_URL).mock(return_value=httpx.Response(200, json={"unexpected": "shape"}))
    resp = await client.call("q", "corr", "alice")
    assert resp.failed is True
    assert "not a usable string" in resp.error


@respx.mock
async def test_empty_content_is_a_failure_not_a_wrong_answer(client):
    # Judging "" would produce a meaningless incorrect verdict and hide the
    # actual problem, so the question is failed instead.
    respx.post(EXECUTE_URL).mock(return_value=httpx.Response(200, json={"content": ""}))
    resp = await client.call("q", "corr", "alice")
    assert resp.failed is True
    assert "empty response" in resp.error


@respx.mock
async def test_redirect_is_followed_not_treated_as_the_response(client):
    # Some servers register the route with a trailing slash, so a POST to
    # /execute comes back as a 307 to /execute/ (httpx does not follow
    # redirects by default). Confirm we follow it instead of parsing the
    # redirect's (empty) body as the answer.
    respx.post(EXECUTE_URL).mock(
        return_value=httpx.Response(307, headers={"location": f"{EXECUTE_URL}/"})
    )
    respx.post(f"{EXECUTE_URL}/").mock(return_value=httpx.Response(200, json={"content": "hi"}))
    resp = await client.call("q", "corr", "alice")
    assert resp.failed is False
    assert resp.response == "hi"


@respx.mock
async def test_5xx_raises_so_the_orchestrator_can_retry(client):
    respx.post(EXECUTE_URL).mock(return_value=httpx.Response(503, text="unavailable"))
    with pytest.raises(AgentHttpError):
        await client.call("q", "corr", "alice")


@respx.mock
async def test_4xx_fails_the_question_without_retrying(client):
    respx.post(EXECUTE_URL).mock(return_value=httpx.Response(400, text="bad request"))
    resp = await client.call("q", "corr", "alice")
    assert resp.failed is True
    assert "400" in resp.error


@respx.mock
async def test_per_run_base_url_and_timeout_override_the_environment(configure):
    # The run, not the process, decides which agent server a question goes to.
    other = "https://agent-b.test"
    with configure(agent_base_url=URL, agent_timeout_s=5.0):
        c = HttpAgentClient(base_url=other, timeout_s=1.5)
        respx.post(f"{other}/execute").mock(
            return_value=httpx.Response(200, json={"content": "x"})
        )
        resp = await c.call("q", "corr", "alice")

    assert resp.failed is False
    assert str(respx.calls[0].request.url) == f"{other}/execute"
    assert c.timeout_s == 1.5
