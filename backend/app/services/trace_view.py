"""Reading a trace for the *view* path, and turning its spans into API output.

Two functions, both previously copied per router. `results.py` and `diagnosis.py`
each had their own near-identical trace poll, and the playground (§10) would have
made three; the span mapping had one copy plus a load-bearing rule (below) that
nothing enforced.

The distinction that matters: this is the **view** path, not the diagnosis path.
Here the trace is read for a developer to look at, so it is never truncated —
§6.7 truncation exists for an LLM's context window, and applying it here shredded
the very evidence a span was opened to read (§9.19).
"""
from __future__ import annotations

import asyncio
from typing import NamedTuple

from app.config import settings
from app.integrations.base import NotReady, Span, TraceFetchError
from app.schemas import SpanOut


class TraceRead(NamedTuple):
    """What a view-path read of the trace store came back with.

    `fatal` separates "this will never work until someone fixes something"
    (unreachable host, rejected key) from a `partial` failure, where one read
    path broke but another said the trace simply hasn't been ingested yet. The
    second is still worth showing — it names a genuinely broken Langfuse
    endpoint — but as context under "generating", not as a dead end, because the
    trace usually does arrive a moment later.
    """

    trace: object | None
    error: str | None
    fatal: bool = True


async def resolve_trace_spans(correlation_id: str, trace_client) -> TraceRead:
    """Light poll of the trace store for a request path.

    The error is returned rather than swallowed: an unreachable Langfuse, a
    rejected key and a trace that is still being ingested all produce "no
    spans", and showing the same "still generating" message for all three is
    indistinguishable from the platform being broken.

    Short sleeps on purpose: this runs inside a request, so it must not block for
    the orchestrator's much longer ingestion backoff. If the trace still isn't
    there the caller reports "generating" (or 409) and the user retries.
    """
    partial_error: str | None = None
    for _ in range(settings.trace_poll_max_attempts):
        try:
            trace = await trace_client.fetch_trace(correlation_id)
        except TraceFetchError as exc:
            if not getattr(exc, "partial", False):
                return TraceRead(None, f"{type(exc).__name__}: {exc}")
            # Keep polling: the endpoint that answered said "not yet".
            partial_error = f"{type(exc).__name__}: {exc}"
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            return TraceRead(None, f"{type(exc).__name__}: {exc}")
        else:
            if not isinstance(trace, NotReady):
                return TraceRead(trace, None)
        await asyncio.sleep(0.05)
    return TraceRead(None, partial_error, fatal=False)


def span_to_out(span: Span) -> SpanOut:
    """One span as the API returns it: full body, structured where it was.

    The two `*_json` fallbacks are the load-bearing part. Langfuse holds whatever
    the agent SDK handed it, and when that was a chat-completions request/response
    the UI renders it per message rather than dumping JSON; `input`/`output` stay
    text only as the fallback, since that is the form the diagnosis prompt is
    built from.
    """
    return SpanOut(
        index=span.index,
        tool_name=span.tool_name,
        status=span.status,
        input=span.input_json if span.input_json is not None else span.input,
        output=span.output_json if span.output_json is not None else span.output,
        token_usage=span.token_usage,
        status_message=span.status_message,
    )
