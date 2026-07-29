"""Real TraceClient: read a trace back out of Langfuse (§3.1 / §6.2).

Basic auth (public key : secret key). The correlation_id is the trace id because
the agent server pins it there when it receives the question (§6.2).

**Two read strategies, because one endpoint is not always enough.** Langfuse
exposes the same observations through two APIs that its server answers with
*different* internal queries:

    trace_api         GET /api/public/traces/{traceId}
                      -> TraceWithFullDetails, whose `observations` are full
                         objects with exactly the fields mapped below
    observations_api  GET /api/public/v2/observations?traceId={traceId}
                      -> the paginated observation list

Self-hosted Langfuse builds from ~3.152.0 can fail one of these with a raw
ClickHouse error — `Unknown table expression 'events'` / `'events_core'` —
because the query targets the v4 wide-observations schema whose production
migration has not shipped (langfuse#11924, langfuse#12223, discussion#12777).
That is a fault in the Langfuse deployment, not in this client: we send no SQL,
Langfuse generates it. The definitive fix is on their side (re-run the ClickHouse
migrations, or pin the image below 3.152) — trying both endpoints is a hedge
that costs one extra request and often works. `LANGFUSE_TRACE_READ_STRATEGY`
pins a single strategy once a deployment is known-good.

Stage 1 renders a flat, time-ordered span list, so the `parentObservationId` in
the payload goes unused — rebuilding the tree belongs with the Stage 2 heatmap.

The NotReady contract is what makes §6.12 work: Langfuse ingestion is async, so
a trace requested right after a run finishes may legitimately not exist yet. Zero
observations — or a 404 from the single-trace endpoint — means "not ingested
yet", and the existing poll + backoff in the orchestrator and the view path
handle the wait unchanged.

Anything that is *not* that — an unreachable host, rejected credentials, a
timeout, a server-side SQL error — raises `TraceFetchError` with the host, the
status code and a snippet of the response body, so the UI can say what actually
went wrong instead of showing the same "still ingesting" message forever. When
every strategy fails, all of their errors are reported: a fallback must never
hide the reason the primary path failed.
"""
from __future__ import annotations

import httpx

from app.config import settings
from app.integrations.base import NOT_READY, NotReady, Span, Trace, TraceFetchError
from app.integrations.real.llm import as_text

_PAGE_LIMIT = 100
# Stop paging even if Langfuse keeps claiming more; a single eval question should
# never produce this many observations, and an unbounded loop here would hang a run.
_MAX_PAGES = 20


_ERROR_BODY_MAX_CHARS = 200


def _body_snippet(resp: httpx.Response) -> str:
    try:
        text = resp.text
    except Exception:  # noqa: BLE001 - a body we can't read is not worth failing over
        return "<unreadable response body>"
    text = " ".join(text.split())
    if len(text) > _ERROR_BODY_MAX_CHARS:
        text = text[:_ERROR_BODY_MAX_CHARS] + "…"
    return text or "<empty response body>"


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
    def __init__(
        self,
        host: str | None = None,
        public_key: str | None = None,
        secret_key: str | None = None,
        timeout_s: float | None = None,
    ) -> None:
        self.host = (host or settings.langfuse_host).rstrip("/")
        if not self.host:
            raise RuntimeError(
                "TRACE_IMPL=real but no Langfuse host was given — set it in the run "
                "config, or via LANGFUSE_HOST."
            )
        self.public_key = public_key or settings.langfuse_public_key
        self.secret_key = secret_key or settings.langfuse_secret_key
        if not (self.public_key and self.secret_key):
            raise RuntimeError(
                "TRACE_IMPL=real but the Langfuse public/secret key pair is "
                "incomplete — set it in the run config, or via "
                "LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY."
            )
        self.timeout_s = timeout_s or settings.langfuse_timeout_s

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self.timeout_s, auth=(self.public_key, self.secret_key)
        )

    def _http_error(self, resp: httpx.Response) -> TraceFetchError:
        # The body is where Langfuse says *why* — "invalid credentials",
        # "project not found", or the ClickHouse error described in the module
        # docstring. A bare status code sends the developer looking in the
        # wrong place.
        return TraceFetchError(
            f"Langfuse at {self.host} returned HTTP {resp.status_code}: "
            f"{_body_snippet(resp)}"
        )

    async def _via_trace_api(self, correlation_id: str) -> list[dict] | NotReady:
        """GET /api/public/traces/{id} -> TraceWithFullDetails.

        Its `observations` are full observation objects (Langfuse's
        `ObservationsView`), carrying the same fields `observation_to_span`
        reads off the list endpoint, so the mapping below is shared verbatim.
        """
        url = f"{self.host}/api/public/traces/{correlation_id}"
        try:
            async with self._client() as client:
                resp = await client.get(url)
                if resp.status_code == 404:
                    # No such trace *yet*. Ingestion is async (§6.12), so this is
                    # "come back later", not a failure.
                    return NOT_READY
                if resp.status_code >= 400:
                    raise self._http_error(resp)
                body = resp.json()
        except httpx.HTTPError as exc:
            raise TraceFetchError(
                f"Could not reach Langfuse at {self.host}: {type(exc).__name__}: {exc}"
            ) from exc

        observations = body.get("observations")
        if not isinstance(observations, list):
            return []
        return [o for o in observations if isinstance(o, dict)]

    async def _via_observations_api(self, correlation_id: str) -> list[dict]:
        """GET /api/public/v2/observations?traceId= — the paginated list."""
        url = f"{self.host}/api/public/v2/observations"
        collected: list[dict] = []

        try:
            async with self._client() as client:
                for page in range(1, _MAX_PAGES + 1):
                    resp = await client.get(
                        url,
                        params={"traceId": correlation_id, "page": page, "limit": _PAGE_LIMIT},
                    )
                    if resp.status_code >= 400:
                        raise self._http_error(resp)
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
        except httpx.HTTPError as exc:
            raise TraceFetchError(
                f"Could not reach Langfuse at {self.host}: {type(exc).__name__}: {exc}"
            ) from exc
        return collected

    def _strategies(self) -> list[tuple[str, object]]:
        chosen = (settings.langfuse_trace_read_strategy or "auto").strip().lower()
        available = {
            "trace_api": self._via_trace_api,
            "observations_api": self._via_observations_api,
        }
        if chosen in available:
            return [(chosen, available[chosen])]
        # `auto`: the single-trace endpoint first. It is one request rather than
        # a paginated loop, and it is the one that tends to survive on the
        # self-hosted builds affected by the `events` table bug.
        return list(available.items())

    async def _fetch_observations(self, correlation_id: str) -> list[dict] | NotReady:
        errors: list[str] = []
        saw_not_ready = False

        for name, strategy in self._strategies():
            try:
                result = await strategy(correlation_id)
            except TraceFetchError as exc:
                # Remember and fall through: a second endpoint may be served by a
                # query the first one couldn't run.
                errors.append(f"[{name}] {exc}")
                continue
            if isinstance(result, NotReady):
                saw_not_ready = True
                continue
            if result:
                return result
            # An empty-but-successful read is also "nothing ingested yet".
            saw_not_ready = True

        if errors and not saw_not_ready:
            # Every strategy failed outright. Report all of them — hiding the
            # first failure behind the last is how a fallback turns one clear
            # error into a confusing one.
            raise TraceFetchError(
                "Could not read the trace from Langfuse. " + " | ".join(errors)
            )
        if errors:
            # Mixed: something answered "not ingested yet" while something else
            # errored. Treat it as not-ready (the caller retries) but keep the
            # error visible in the message the orchestrator records.
            raise TraceFetchError(
                "Langfuse partially failed while reading the trace. "
                + " | ".join(errors)
            )
        return NOT_READY

    async def fetch_trace(self, correlation_id: str) -> Trace | NotReady:
        observations = await self._fetch_observations(correlation_id)
        if isinstance(observations, NotReady):
            return NOT_READY

        wanted = {t.upper() for t in settings.langfuse_observation_types}
        if wanted:
            observations = [
                o for o in observations if (o.get("type") or "").upper() in wanted
            ]

        if not observations:
            # Either ingestion hasn't landed yet, or this trace genuinely has no
            # observations of a type we render. Both are "come back later" from
            # the caller's side; the poll cap decides when to give up (§6.12).
            return NOT_READY

        # Time order is the execution order the developer expects to read.
        observations.sort(key=lambda o: (o.get("startTime") or "", o.get("id") or ""))
        spans = [observation_to_span(obs, i) for i, obs in enumerate(observations)]
        return Trace(correlation_id=correlation_id, spans=spans)
