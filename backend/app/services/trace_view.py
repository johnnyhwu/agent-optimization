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
        latency_ms=span.latency_ms,
    )


def count_llm_calls(trace) -> int | None:
    """How many of a trace's spans were model calls.

    A trace interleaves two kinds of step: calls to the model, and the tool
    invocations they ask for. Only the first kind costs tokens, and only the
    first kind is what someone means by "how many LLM calls did this question
    take" — a question that made one model call and eleven tool calls is a
    different problem from one that made eleven of each.

    **Token usage is the test.** A generation reports what it spent; a tool
    invocation has nothing to report and so reports nothing. That is a property
    of what the two things *are* rather than of any particular agent's naming, so
    it survives instrumentation that labels every span `OpenAI Completion` —
    which, per `span_label.js`, is the normal case rather than the broken one.

    `None` for a trace we never got, which is not the same as a trace showing no
    model calls: one is "we do not know", the other would be "the agent answered
    without asking anything", and the second is a claim worth not making by
    accident.
    """
    if trace is None or not trace.spans:
        return None
    return sum(1 for span in trace.spans if _spent_tokens(span))


def _spent_tokens(span: Span) -> bool:
    usage = span.token_usage or {}
    return any(isinstance(usage.get(k), int) and usage[k] > 0
               for k in ("input", "output", "total"))
