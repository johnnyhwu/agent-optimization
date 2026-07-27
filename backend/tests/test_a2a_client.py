"""A2A agent client: request shape (esp. the correlation metadata) and the
several response shapes a server may legitimately return."""
from __future__ import annotations

import httpx
import pytest
import respx

from app.integrations.real.a2a import A2AAgentClient, A2AError, extract_response_text

URL = "https://agent.test/a2a"


@pytest.fixture
def client(configure):
    with configure(a2a_base_url=URL, a2a_api_key="", a2a_timeout_s=5.0):
        yield A2AAgentClient()


def _rpc(result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": "1", "result": result}


@respx.mock
async def test_request_carries_correlation_id_in_metadata(client, configure):
    route = respx.post(URL).mock(
        return_value=httpx.Response(200, json=_rpc({"parts": [{"kind": "text", "text": "hi"}]}))
    )
    await client.call("What is 2+2?", "corr-abc")

    body = route.calls[0].request.read().decode()
    assert '"trace_id": "corr-abc"' in body.replace('":"', '": "')
    assert "What is 2+2?" in body
    assert '"method": "message/send"' in body.replace('":"', '": "')


@respx.mock
async def test_metadata_key_is_configurable(configure):
    with configure(a2a_base_url=URL, a2a_correlation_metadata_key="eval_correlation_id"):
        c = A2AAgentClient()
        route = respx.post(URL).mock(
            return_value=httpx.Response(200, json=_rpc({"parts": [{"kind": "text", "text": "x"}]}))
        )
        await c.call("q", "corr-1")
        payload = route.calls[0].request.read().decode()
        assert "eval_correlation_id" in payload
        assert "trace_id" not in payload


@respx.mock
async def test_message_shape_response(client):
    respx.post(URL).mock(
        return_value=httpx.Response(200, json=_rpc({"parts": [{"kind": "text", "text": "four"}]}))
    )
    resp = await client.call("q", "corr")
    assert resp.failed is False
    assert resp.response == "four"
    assert resp.latency_ms is not None


@respx.mock
async def test_task_shape_prefers_artifacts_over_status_message(client):
    respx.post(URL).mock(
        return_value=httpx.Response(
            200,
            json=_rpc(
                {
                    "status": {"message": {"parts": [{"kind": "text", "text": "completed"}]}},
                    "artifacts": [{"parts": [{"kind": "text", "text": "the answer"}]}],
                }
            ),
        )
    )
    resp = await client.call("q", "corr")
    assert resp.response == "the answer"


@respx.mock
async def test_task_falls_back_to_status_message(client):
    respx.post(URL).mock(
        return_value=httpx.Response(
            200,
            json=_rpc({"status": {"message": {"parts": [{"kind": "text", "text": "answer"}]}}}),
        )
    )
    resp = await client.call("q", "corr")
    assert resp.response == "answer"


@respx.mock
async def test_jsonrpc_error_is_a_failed_question_not_an_exception(client):
    respx.post(URL).mock(
        return_value=httpx.Response(
            200, json={"jsonrpc": "2.0", "id": "1", "error": {"code": -32000, "message": "boom"}}
        )
    )
    resp = await client.call("q", "corr")
    assert resp.failed is True
    assert "boom" in resp.error


@respx.mock
async def test_empty_text_is_a_failure_not_a_wrong_answer(client):
    # Judging "" would produce a meaningless incorrect verdict and hide the
    # actual problem, so the question is failed instead.
    respx.post(URL).mock(return_value=httpx.Response(200, json=_rpc({"parts": []})))
    resp = await client.call("q", "corr")
    assert resp.failed is True
    assert "no text parts" in resp.error


@respx.mock
async def test_5xx_raises_so_the_orchestrator_can_retry(client):
    respx.post(URL).mock(return_value=httpx.Response(503, text="unavailable"))
    with pytest.raises(A2AError):
        await client.call("q", "corr")


@respx.mock
async def test_4xx_fails_the_question_without_retrying(client):
    respx.post(URL).mock(return_value=httpx.Response(400, text="bad request"))
    resp = await client.call("q", "corr")
    assert resp.failed is True
    assert "400" in resp.error


@respx.mock
async def test_auth_header_applied(configure):
    with configure(a2a_base_url=URL, a2a_api_key="s3cret"):
        c = A2AAgentClient()
        route = respx.post(URL).mock(
            return_value=httpx.Response(200, json=_rpc({"parts": [{"kind": "text", "text": "x"}]}))
        )
        await c.call("q", "corr")
        assert route.calls[0].request.headers["Authorization"] == "Bearer s3cret"


def test_extract_handles_legacy_type_field():
    assert extract_response_text({"parts": [{"type": "text", "text": "legacy"}]}) == "legacy"


def test_extract_ignores_non_text_parts():
    result = {"parts": [{"kind": "file", "file": {}}, {"kind": "text", "text": "keep"}]}
    assert extract_response_text(result) == "keep"
