"""Real AgentClient: a plain HTTP agent server (§6.2).

The agent server is a small FastAPI app with a single `POST /execute` endpoint
that takes `{"message": str, "metadata": dict}` and returns `{"content": str}`
with the agent's answer. No protocol SDK is involved — the payload and
response are both trivial, so a hand-written httpx POST is simpler than
depending on one.

The correlation mechanism (§6.2 / §6.7) is the whole point of this client: the
platform mints a correlation_id per question and puts it in
`metadata.trace_data.trace_id` (and reuses it as `session_id`, since each
question is its own Langfuse session); the agent server applies it as its
Langfuse trace id, which is how the trace is found again later.
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from app.config import settings
from app.integrations.base import AgentResponse


class AgentHttpError(RuntimeError):
    """The agent server answered, but with a 5xx we want the retry loop to see."""


def _extract_text(resp: httpx.Response) -> str | None:
    """Pull the answer out of an `/execute` response body: `{"content": str}`.

    A bare JSON string is also accepted (some servers skip the wrapper), and a
    non-JSON body falls back to the raw response text. Anything else — a dict
    without a string `content`, or another JSON shape entirely — is not a
    usable answer.
    """
    try:
        body = resp.json()
    except ValueError:
        return resp.text
    if isinstance(body, dict):
        content = body.get("content")
        return content if isinstance(content, str) else None
    if isinstance(body, str):
        return body
    return None


class HttpAgentClient:
    """POST a question to the agent server's /execute endpoint and return its answer."""

    def __init__(self, base_url: str | None = None, timeout_s: float | None = None) -> None:
        self.base_url = (base_url or settings.agent_base_url).rstrip("/")
        if not self.base_url:
            raise RuntimeError(
                "AGENT_IMPL=real but no agent base URL was given — set it in the "
                "run config, or via AGENT_BASE_URL (e.g. http://agent-host:8080)."
            )
        self.timeout_s = timeout_s or settings.agent_timeout_s

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    def build_payload(
        self, question: str, correlation_id: str, user_id: str,
        tags: list[str] | None,
    ) -> dict[str, Any]:
        return {
            "message": question,
            "metadata": {
                "trace_data": {
                    "trace_id": correlation_id,
                    # Same value as trace_id: each question is its own
                    # correlation unit, so it is also its own Langfuse session.
                    "session_id": correlation_id,
                    "user_id": user_id,
                    "tags": tags or [],
                },
            },
        }

    async def call(
        self, question: str, correlation_id: str, user_id: str,
        tags: list[str] | None = None,
    ) -> AgentResponse:
        payload = self.build_payload(question, correlation_id, user_id, tags)
        started = time.monotonic()

        async with httpx.AsyncClient(
            timeout=self.timeout_s, follow_redirects=True
        ) as client:
            resp = await client.post(
                f"{self.base_url}/execute", json=payload, headers=self._headers()
            )

        latency_ms = int((time.monotonic() - started) * 1000)

        # Let 5xx/timeouts raise so the orchestrator's retry policy sees them;
        # a 4xx is a request problem and will fail identically on every retry.
        if resp.status_code >= 500:
            raise AgentHttpError(f"agent server returned {resp.status_code}: {resp.text[:500]}")
        if resp.status_code >= 400:
            return AgentResponse(
                response="", correlation_id=correlation_id, failed=True,
                error=f"agent server returned {resp.status_code}: {resp.text[:500]}",
                latency_ms=latency_ms,
            )

        text = _extract_text(resp)
        if text is None:
            return AgentResponse(
                response="", correlation_id=correlation_id, failed=True,
                error=f"/execute response was not a usable string: {resp.text[:500]}",
                latency_ms=latency_ms,
            )

        text = text.strip()
        if not text:
            # An empty answer is a failure, not a wrong answer: judging "" would
            # produce a meaningless incorrect verdict and hide the real problem.
            return AgentResponse(
                response="", correlation_id=correlation_id, failed=True,
                error="/execute returned an empty response.", latency_ms=latency_ms,
            )

        return AgentResponse(response=text, correlation_id=correlation_id, latency_ms=latency_ms)
