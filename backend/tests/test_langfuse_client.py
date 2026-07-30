"""Langfuse trace client: the NotReady contract that §6.12 depends on, paging,
the observation -> Span mapping across Langfuse schema versions, and the two
read strategies that let one broken endpoint be routed around."""
from __future__ import annotations

import httpx
import pytest
import respx

from app.integrations.base import NotReady, TraceFetchError
from app.integrations.real.langfuse import LangfuseTraceClient, observation_to_span

HOST = "https://langfuse.test"
OBS_URL = f"{HOST}/api/public/v2/observations"
TRACE_URL = f"{HOST}/api/public/traces/corr"

# The real body a self-hosted Langfuse ≥3.152 returns when its ClickHouse is
# missing the v4 `events` table (langfuse#11924).
EVENTS_TABLE_ERROR = (
    "SQL Error: Unknown table expression 'events' in scope SELECT e._span_id AS id, "
    "e.trace_id AS trace_id"
)


def _client(configure, strategy: str):
    return configure(
        langfuse_host=HOST,
        langfuse_public_key="pk",
        langfuse_secret_key="sk",
        langfuse_observation_types=["GENERATION", "SPAN"],
        langfuse_trace_read_strategy=strategy,
    )


@pytest.fixture
def client(configure):
    # Pinned to the list endpoint: these cases are about *its* paging, auth and
    # error handling. Strategy selection has its own tests below.
    with _client(configure, "observations_api"):
        yield LangfuseTraceClient()


@pytest.fixture
def trace_api_client(configure):
    with _client(configure, "trace_api"):
        yield LangfuseTraceClient()


@pytest.fixture
def auto_client(configure):
    with _client(configure, "auto"):
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


# --- The structured body survives the mapping --------------------------------
# The text rendering is what the diagnosis prompt is built from; the object is
# what the span view renders per message. Flattening to text only, as this
# client used to, left the UI with a JSON dump of an LLM call.

def test_structured_body_is_kept_alongside_the_text():
    request = {"tools": [{"type": "function"}], "messages": [{"role": "user", "content": "hi"}]}
    span = observation_to_span(_obs(input=request, output={"role": "assistant"}), 0)
    assert span.input_json == request
    assert span.output_json == {"role": "assistant"}
    assert "hi" in span.input  # …and the text form is untouched


def test_a_body_logged_as_a_json_string_is_parsed():
    """Some agents hand Langfuse an already-serialized payload."""
    span = observation_to_span(_obs(input='{"messages": [{"role": "user"}]}'), 0)
    assert span.input_json == {"messages": [{"role": "user"}]}


def test_prose_bodies_have_no_structured_form():
    span = observation_to_span(_obs(input="just a sentence", output="{not json"), 0)
    assert span.input_json is None
    assert span.output_json is None


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


# --- Read strategies ---------------------------------------------------------
#
# Two endpoints expose the same observations through different server-side
# queries, so a Langfuse that can serve one but not the other (the `events`
# table bug) is still usable.

@respx.mock
async def test_trace_api_returns_the_same_spans_as_the_list_endpoint(trace_api_client):
    """GET /traces/{id} embeds full observation objects, so the mapper is shared.

    If this ever diverges from the list endpoint's shape, the fallback would
    silently produce a different-looking trace.
    """
    respx.get(TRACE_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "corr",
                "observations": [
                    _obs(id="b", name="second", startTime="2026-07-27T00:00:02Z"),
                    _obs(id="a", name="first", startTime="2026-07-27T00:00:01Z"),
                ],
            },
        )
    )
    trace = await trace_api_client.fetch_trace("corr")
    assert [s.index for s in trace.spans] == [0, 1]
    assert [s.tool_name for s in trace.spans] == ["first", "second"]


@respx.mock
async def test_trace_api_404_is_not_ready_not_an_error(trace_api_client):
    """Ingestion is async: a trace that doesn't exist yet must keep the caller
    polling (§6.12), not fail the question."""
    respx.get(TRACE_URL).mock(return_value=httpx.Response(404, json={"message": "not found"}))
    assert isinstance(await trace_api_client.fetch_trace("corr"), NotReady)


@respx.mock
async def test_auto_tries_the_trace_api_first_and_stops_there(auto_client):
    trace_route = respx.get(TRACE_URL).mock(
        return_value=httpx.Response(200, json={"observations": [_obs()]})
    )
    obs_route = respx.get(OBS_URL).mock(
        return_value=httpx.Response(200, json={"data": [], "meta": {}})
    )
    trace = await auto_client.fetch_trace("corr")
    assert [s.tool_name for s in trace.spans] == ["sql_query"]
    assert trace_route.called
    assert not obs_route.called  # no wasted second request on the happy path


@respx.mock
async def test_auto_falls_back_when_the_first_endpoint_breaks(auto_client):
    """The point of the fallback: the broken ClickHouse query is per-endpoint."""
    respx.get(TRACE_URL).mock(return_value=httpx.Response(500, text=EVENTS_TABLE_ERROR))
    respx.get(OBS_URL).mock(
        return_value=httpx.Response(200, json={"data": [_obs()], "meta": {"totalPages": 1}})
    )
    trace = await auto_client.fetch_trace("corr")
    assert [s.tool_name for s in trace.spans] == ["sql_query"]


@respx.mock
async def test_auto_reports_every_endpoint_that_failed(auto_client):
    """A fallback must not hide why the primary path failed."""
    respx.get(TRACE_URL).mock(return_value=httpx.Response(500, text=EVENTS_TABLE_ERROR))
    respx.get(OBS_URL).mock(return_value=httpx.Response(401, json={"message": "invalid credentials"}))
    with pytest.raises(TraceFetchError) as exc:
        await auto_client.fetch_trace("corr")
    message = str(exc.value)
    assert "trace_api" in message and "observations_api" in message
    assert "Unknown table expression" in message  # the primary failure survives
    assert "invalid credentials" in message


@respx.mock
async def test_auto_keeps_polling_when_one_endpoint_says_not_ready(auto_client):
    """A 404 (not ingested yet) plus an outright failure must not be reported as
    a clean NotReady — the developer still needs to see the broken endpoint."""
    respx.get(TRACE_URL).mock(return_value=httpx.Response(404, json={"message": "not found"}))
    respx.get(OBS_URL).mock(return_value=httpx.Response(500, text=EVENTS_TABLE_ERROR))
    with pytest.raises(TraceFetchError) as exc:
        await auto_client.fetch_trace("corr")
    assert "observations_api" in str(exc.value)


@respx.mock
async def test_auto_is_not_ready_when_both_endpoints_are_simply_empty(auto_client):
    respx.get(TRACE_URL).mock(return_value=httpx.Response(404))
    respx.get(OBS_URL).mock(return_value=httpx.Response(200, json={"data": [], "meta": {}}))
    assert isinstance(await auto_client.fetch_trace("corr"), NotReady)
