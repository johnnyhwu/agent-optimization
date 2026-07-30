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

from app.config import settings
from app.integrations.base import NotReady, Span
from app.schemas import SpanOut


async def resolve_trace_spans(correlation_id: str, trace_client):
    """Light poll of the trace store for a request path.

    Returns (trace_or_None, error_or_None). The error is returned rather than
    swallowed: an unreachable Langfuse, a rejected key and a trace that is still
    being ingested all produce "no spans", and showing the same "still
    generating" message for all three is indistinguishable from the platform
    being broken.

    Short sleeps on purpose: this runs inside a request, so it must not block for
    the orchestrator's much longer ingestion backoff. If the trace still isn't
    there the caller reports "generating" (or 409) and the user retries.
    """
    for _ in range(settings.trace_poll_max_attempts):
        try:
            trace = await trace_client.fetch_trace(correlation_id)
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            return None, f"{type(exc).__name__}: {exc}"
        if not isinstance(trace, NotReady):
            return trace, None
        await asyncio.sleep(0.05)
    return None, None


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
