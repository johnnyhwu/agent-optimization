"""Real AgentClient: the A2A server hosting the domain agent (§6.2).

Hand-written JSON-RPC 2.0 over httpx rather than a protocol SDK — the payload is
small, and when a server returns a shape we didn't expect we want to see the raw
body, not an SDK validation error.

The correlation mechanism (§6.2 / §6.7) is the whole point of this client: the
platform mints a correlation_id per question and puts it in the request metadata
under `a2a_correlation_metadata_key` (default `trace_id`); the agent server
applies it as its Langfuse trace id, which is how the trace is found again later.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

import httpx

from app.config import settings
from app.integrations.base import AgentResponse


class A2AError(RuntimeError):
    """The A2A server answered, but with a JSON-RPC error or an unusable body."""


def _collect_text(parts: Any) -> list[str]:
    """Pull the text out of an A2A `parts` array, ignoring non-text parts."""
    out: list[str] = []
    if not isinstance(parts, list):
        return out
    for part in parts:
        if not isinstance(part, dict):
            continue
        # `kind` is the current field name; `type` appears in older servers.
        if part.get("kind") in (None, "text") or part.get("type") == "text":
            text = part.get("text")
            if isinstance(text, str) and text:
                out.append(text)
    return out


def extract_response_text(result: dict) -> str:
    """Flatten an A2A result into the agent's answer.

    Accepts both shapes a server may return from `message/send`:
      * a Message   -> {"parts": [...]}
      * a Task      -> {"status": {"message": {"parts": [...]}}, "artifacts": [...]}
    Artifacts come first when present: a Task that produced artifacts carries its
    answer there, while status.message is often just a completion notice.
    """
    chunks: list[str] = []

    for artifact in result.get("artifacts") or []:
        if isinstance(artifact, dict):
            chunks += _collect_text(artifact.get("parts"))

    if not chunks:
        status = result.get("status")
        if isinstance(status, dict):
            message = status.get("message")
            if isinstance(message, dict):
                chunks += _collect_text(message.get("parts"))

    if not chunks:
        chunks += _collect_text(result.get("parts"))

    return "\n".join(chunks).strip()


class A2AAgentClient:
    """POST a question to the A2A server and return its answer."""

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.a2a_base_url).rstrip("/")
        if not self.base_url:
            raise RuntimeError(
                "AGENT_IMPL=real but A2A_BASE_URL is empty — set it to the A2A "
                "server's JSON-RPC endpoint."
            )

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if settings.a2a_api_key:
            value = settings.a2a_api_key
            if settings.a2a_auth_scheme:
                value = f"{settings.a2a_auth_scheme} {value}"
            headers[settings.a2a_auth_header] = value
        return headers

    def build_payload(self, question: str, correlation_id: str) -> dict:
        return {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex,
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"kind": "text", "text": question}],
                    "messageId": uuid.uuid4().hex,
                },
                # §6.2: the agent server reads this and pins its Langfuse trace id.
                "metadata": {settings.a2a_correlation_metadata_key: correlation_id},
            },
        }

    async def call(self, question: str, correlation_id: str) -> AgentResponse:
        payload = self.build_payload(question, correlation_id)
        started = time.monotonic()

        async with httpx.AsyncClient(timeout=settings.a2a_timeout_s) as client:
            resp = await client.post(self.base_url, json=payload, headers=self._headers())

        latency_ms = int((time.monotonic() - started) * 1000)

        # Let 5xx/timeouts raise so the orchestrator's retry policy sees them;
        # a 4xx is a request problem and will fail identically on every retry.
        if resp.status_code >= 500:
            raise A2AError(f"A2A server returned {resp.status_code}: {resp.text[:500]}")
        if resp.status_code >= 400:
            return AgentResponse(
                response="", correlation_id=correlation_id, failed=True,
                error=f"A2A server returned {resp.status_code}: {resp.text[:500]}",
                latency_ms=latency_ms,
            )

        try:
            body = resp.json()
        except ValueError:
            return AgentResponse(
                response="", correlation_id=correlation_id, failed=True,
                error=f"A2A response was not JSON: {resp.text[:500]}",
                latency_ms=latency_ms,
            )

        if isinstance(body, dict) and body.get("error"):
            err = body["error"]
            detail = err.get("message") if isinstance(err, dict) else str(err)
            return AgentResponse(
                response="", correlation_id=correlation_id, failed=True,
                error=f"A2A JSON-RPC error: {detail}", latency_ms=latency_ms,
            )

        result = body.get("result") if isinstance(body, dict) else None
        if not isinstance(result, dict):
            return AgentResponse(
                response="", correlation_id=correlation_id, failed=True,
                error=f"A2A response had no usable result: {str(body)[:500]}",
                latency_ms=latency_ms,
            )

        text = extract_response_text(result)
        if not text:
            # An empty answer is a failure, not a wrong answer: judging "" would
            # produce a meaningless incorrect verdict and hide the real problem.
            return AgentResponse(
                response="", correlation_id=correlation_id, failed=True,
                error="A2A result contained no text parts.", latency_ms=latency_ms,
            )

        return AgentResponse(
            response=text, correlation_id=correlation_id, latency_ms=latency_ms
        )
