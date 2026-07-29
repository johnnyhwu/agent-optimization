"""Langfuse trace client: the NotReady contract that §6.12 depends on, paging,
and the observation -> Span mapping across Langfuse schema versions."""
from __future__ import annotations

import httpx
import pytest
import respx

from app.integrations.base import NotReady, TraceFetchError
from app.integrations.real.langfuse import LangfuseTraceClient, observation_to_span

HOST = "https://langfuse.test"
OBS_URL = f"{HOST}/api/public/v2/observations"


@pytest.fixture
def client(configure):
    with configure(
        langfuse_host=HOST,
        langfuse_public_key="pk",
        langfuse_secret_key="sk",
        langfuse_observation_types=["GENERATION", "SPAN"],
    ):
        yield LangfuseTraceClient()


def _obs(**kwargs) -> dict:
    base = {
        "id": "o1",
        "type": "GENERATION",
        "name": "sql_query",
        "startTime": "2026-07-27T00:00:00Z",
        "input": "in",
        "output": "out",
    }
    base.update(kwargs)
    return base


@respx.mock
async def test_empty_page_means_not_ready(client):
    # Ingestion is async: "no observations yet" must be NotReady, not an empty
    # trace, or the orchestrator would stop polling and skip diagnosis.
    respx.get(OBS_URL).mock(return_value=httpx.Response(200, json={"data": [], "meta": {}}))
    assert isinstance(await client.fetch_trace("corr"), NotReady)


@respx.mock
async def test_spans_are_time_ordered_and_indexed_from_zero(client):
    respx.get(OBS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    _obs(id="b", name="second", startTime="2026-07-27T00:00:02Z"),
                    _obs(id="a", name="first", startTime="2026-07-27T00:00:01Z"),
                ],
                "meta": {"totalPages": 1},
            },
        )
    )
    trace = await client.fetch_trace("corr")
    assert [s.index for s in trace.spans] == [0, 1]
    assert [s.tool_name for s in trace.spans] == ["first", "second"]


@respx.mock
async def test_observation_types_are_filtered(client):
    respx.get(OBS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [_obs(id="a"), _obs(id="b", type="EVENT", name="noise")],
                "meta": {"totalPages": 1},
            },
        )
    )
    trace = await client.fetch_trace("corr")
    assert [s.tool_name for s in trace.spans] == ["sql_query"]


@respx.mock
async def test_all_filtered_out_means_not_ready(client):
    respx.get(OBS_URL).mock(
        return_value=httpx.Response(
            200, json={"data": [_obs(type="EVENT")], "meta": {"totalPages": 1}}
        )
    )
    assert isinstance(await client.fetch_trace("corr"), NotReady)


@respx.mock
async def test_pagination_collects_every_page(client):
    pages = {
        1: {"data": [_obs(id=f"p1-{i}") for i in range(100)], "meta": {"totalPages": 2}},
        2: {"data": [_obs(id="p2-0")], "meta": {"totalPages": 2}},
    }

    def handler(request):
        page = int(request.url.params.get("page", 1))
        return httpx.Response(200, json=pages[page])

    respx.get(OBS_URL).mock(side_effect=handler)
    trace = await client.fetch_trace("corr")
    assert len(trace.spans) == 101


@respx.mock
async def test_traceid_and_basic_auth_are_sent(client):
    route = respx.get(OBS_URL).mock(
        return_value=httpx.Response(200, json={"data": [_obs()], "meta": {"totalPages": 1}})
    )
    await client.fetch_trace("corr-xyz")
    request = route.calls[0].request
    assert request.url.params["traceId"] == "corr-xyz"
    assert request.headers["authorization"].startswith("Basic ")


def test_usage_details_mapping():
    span = observation_to_span(
        _obs(usageDetails={"input": 10, "output": 5, "total": 15}), 0
    )
    assert span.token_usage == {"input": 10, "output": 5, "total": 15}


def test_legacy_usage_mapping_and_derived_total():
    span = observation_to_span(
        _obs(usage={"promptTokens": 7, "completionTokens": 3}), 0
    )
    assert span.token_usage == {"input": 7, "output": 3, "total": 10}


def test_missing_usage_is_empty_not_zeros():
    assert observation_to_span(_obs(), 0).token_usage == {}


def test_error_level_maps_to_error_status_with_message():
    span = observation_to_span(_obs(level="ERROR", statusMessage="tool exploded"), 3)
    assert span.status == "error"
    assert span.status_message == "tool exploded"


def test_structured_input_is_rendered_as_text():
    span = observation_to_span(_obs(input={"query": "select 1"}), 0)
    assert "select 1" in span.input


# --- Failures must be distinguishable from "not ingested yet" ----------------

@respx.mock
async def test_rejected_credentials_raise_with_the_reason(client):
    """A 401 used to surface as the same "trace is generating" banner as async
    ingestion, so a wrong key looked like a trace that was always seconds away."""
    respx.get(OBS_URL).mock(
        return_value=httpx.Response(401, json={"message": "invalid credentials"})
    )
    with pytest.raises(TraceFetchError) as exc:
        await client.fetch_trace("corr")
    message = str(exc.value)
    assert HOST in message          # which Langfuse
    assert "401" in message         # what it said
    assert "invalid credentials" in message  # and why


@respx.mock
async def test_unreachable_host_raises_rather_than_looking_empty(client):
    respx.get(OBS_URL).mock(side_effect=httpx.ConnectError("nodename nor servname"))
    with pytest.raises(TraceFetchError) as exc:
        await client.fetch_trace("corr")
    assert HOST in str(exc.value)
    assert "ConnectError" in str(exc.value)


@respx.mock
async def test_error_body_is_truncated(client):
    respx.get(OBS_URL).mock(return_value=httpx.Response(500, text="x" * 5000))
    with pytest.raises(TraceFetchError) as exc:
        await client.fetch_trace("corr")
    # A 5 KB HTML error page is not a UI message.
    assert len(str(exc.value)) < 400
