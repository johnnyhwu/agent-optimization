"""HTTP agent client: request shape (message + metadata.trace_data) and the
several response shapes the agent server's /execute endpoint may return."""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.integrations.real.agent import AgentHttpError, HttpAgentClient

URL = "https://agent.test"
EXECUTE_URL = f"{URL}/execute"


@pytest.fixture
def client(configure):
    with configure(agent_base_url=URL, agent_timeout_s=5.0):
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
