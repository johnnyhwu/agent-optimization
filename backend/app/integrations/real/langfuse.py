"""Real TraceClient: read a trace back out of Langfuse (§3.1 / §6.2).

`GET /api/public/v2/observations?traceId={correlation_id}` with Basic auth
(public key : secret key). The correlation_id is the trace id because the agent
server pins it there when it receives the question (§6.2).

Stage 1 renders a flat, time-ordered span list, so `parentObservationId` is read
but not used to rebuild a tree — that belongs with the Stage 2 heatmap view.

The NotReady contract is what makes §6.12 work: Langfuse ingestion is async, so
a trace requested right after a run finishes may legitimately not exist yet. Zero
observations means "not ingested yet", and the existing poll + backoff in the
orchestrator and the view path handle the wait unchanged.
"""
from __future__ import annotations

import httpx

from app.config import settings
from app.integrations.base import NOT_READY, NotReady, Span, Trace
from app.integrations.real.llm import as_text

_PAGE_LIMIT = 100
# Stop paging even if Langfuse keeps claiming more; a single eval question should
# never produce this many observations, and an unbounded loop here would hang a run.
_MAX_PAGES = 20


def _token_usage(obs: dict) -> dict:
    """Normalize Langfuse token accounting across schema versions.

    Newer Langfuse reports `usageDetails` {input, output, total, ...}; older
    payloads use `usage` {promptTokens, completionTokens, totalTokens}.
    """
    details = obs.get("usageDetails")
    if isinstance(details, dict) and details:
        usage = {
            "input": details.get("input"),
            "output": details.get("output"),
            "total": details.get("total"),
        }
    else:
        legacy = obs.get("usage") if isinstance(obs.get("usage"), dict) else {}
        usage = {
            "input": legacy.get("promptTokens") or legacy.get("input"),
            "output": legacy.get("completionTokens") or legacy.get("output"),
            "total": legacy.get("totalTokens") or legacy.get("total"),
        }
    if usage["total"] is None and (usage["input"] or usage["output"]):
        usage["total"] = (usage["input"] or 0) + (usage["output"] or 0)
    return {k: v for k, v in usage.items() if v is not None}


def observation_to_span(obs: dict, index: int) -> Span:
    level = (obs.get("level") or "DEFAULT").upper()
    return Span(
        index=index,
        tool_name=obs.get("name") or obs.get("type") or "unknown",
        status="error" if level == "ERROR" else "success",
        input=as_text(obs.get("input")),
        output=as_text(obs.get("output")),
        token_usage=_token_usage(obs),
        status_message=obs.get("statusMessage") or None,
    )


class LangfuseTraceClient:
    def __init__(self, host: str | None = None) -> None:
        self.host = (host or settings.langfuse_host).rstrip("/")
        if not self.host:
            raise RuntimeError("TRACE_IMPL=real but LANGFUSE_HOST is empty.")
        if not (settings.langfuse_public_key and settings.langfuse_secret_key):
            raise RuntimeError(
                "TRACE_IMPL=real but LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY are empty."
            )

    async def _fetch_observations(self, correlation_id: str) -> list[dict]:
        url = f"{self.host}/api/public/v2/observations"
        auth = (settings.langfuse_public_key, settings.langfuse_secret_key)
        collected: list[dict] = []

        async with httpx.AsyncClient(timeout=settings.langfuse_timeout_s, auth=auth) as client:
            for page in range(1, _MAX_PAGES + 1):
                resp = await client.get(
                    url,
                    params={"traceId": correlation_id, "page": page, "limit": _PAGE_LIMIT},
                )
                resp.raise_for_status()
                body = resp.json()
                batch = body.get("data") or []
                collected += [o for o in batch if isinstance(o, dict)]

                meta = body.get("meta") or {}
                total_pages = meta.get("totalPages")
                if total_pages is not None:
                    if page >= total_pages:
                        break
                elif len(batch) < _PAGE_LIMIT:
                    break
        return collected

    async def fetch_trace(self, correlation_id: str) -> Trace | NotReady:
        observations = await self._fetch_observations(correlation_id)

        wanted = {t.upper() for t in settings.langfuse_observation_types}
        if wanted:
            observations = [
                o for o in observations if (o.get("type") or "").upper() in wanted
            ]

        if not observations:
            # Either ingestion hasn't landed yet, or this trace genuinely has no
            # observations. Both are "come back later" from the caller's side;
            # the poll cap decides when to give up (§6.12).
            return NOT_READY

        # Time order is the execution order the developer expects to read.
        observations.sort(key=lambda o: (o.get("startTime") or "", o.get("id") or ""))
        spans = [observation_to_span(obs, i) for i, obs in enumerate(observations)]
        return Trace(correlation_id=correlation_id, spans=spans)
