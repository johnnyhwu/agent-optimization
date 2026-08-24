"""Real AgentClient: a plain HTTP agent server (§6.2).

The agent server is a small FastAPI app with a single `POST /execute` endpoint
that takes `{"message": str, "metadata": dict}` and returns `{"content": str}`
with the agent's answer. No protocol SDK is involved — the payload and
response are both trivial, so a hand-written httpx POST is simpler than
depending on one.

A playground attempt — and every optimization rollout — may also carry
`metadata.skills`, the complete skill file set the agent should use for this one
call. An eval run never sends it, so the run path's request body carries only
what is true of every call.

Every call also carries `metadata.timeout_s`, the budget the agent server should
give itself for this one question — see `server_budget_s` for why it
is not simply this client's own timeout.

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
from app.integrations.base import AgentResponse, WorkspaceOverride


class AgentHttpError(RuntimeError):
    """The agent server answered, but with a 5xx we want the retry loop to see."""


# How much less time the agent server is given than we are prepared to wait.
# Deliberately not a setting: every deployment wants the same thing from it —
# enough room for the far side to finish its own timeout and get a 5xx onto the
# wire before we stop listening — and a value nobody tunes per environment is a
# value that only costs something to keep in sync across .env, compose and
# whatever the deployment sets.
SERVER_TIMEOUT_MARGIN_S = 5.0


def server_budget_s(timeout_s: float, margin_s: float = SERVER_TIMEOUT_MARGIN_S) -> float:
    """The time budget handed to the agent server: our own limit, minus a margin.

    Both ends need a deadline, and they must not be the same number. The agent
    server enforces its own limit on how long one `/execute` may run; without
    being told ours it uses a built-in default, which is why raising the
    platform's timeout past that default used to change nothing. Sending it
    solves that — but sending the *same* value would make "who gives up first" a
    race, and the two outcomes read very differently: the server timing out is
    an answer with a reason in it, while we timing out is a dropped connection.
    So the server is asked to finish first, by `margin_s`.

    Never less than half of `timeout_s`: a margin wider than the timeout itself
    (a 3s timeout against the default 5s margin) must not become a zero or
    negative budget.
    """
    return round(max(timeout_s - max(margin_s, 0.0), timeout_s / 2), 3)


def _looks_like_markup(text: str) -> bool:
    """Does this body open as HTML/XML rather than as an answer?

    The one non-JSON body that must never be passed through. A proxy, gateway
    or web framework answering 200 with an error page is not an agent
    answering — but it arrives on the same code path as a legitimate
    `text/plain` answer, and if it were accepted the judge would grade the
    markup and record a confident wrong verdict against the agent.

    Keyed on how the body *opens*, not on containing a tag anywhere: an answer
    may perfectly well discuss `<b>`.
    """
    return text.lstrip()[:1] == "<"


def _extract_text(resp: httpx.Response) -> str | None:
    """Pull the answer out of an `/execute` response body: `{"content": str}`.

    A bare JSON string is also accepted (some servers skip the wrapper), and a
    non-JSON body falls back to the raw response text unless it opens as markup.
    Anything else — a dict without a string `content`, or another JSON shape
    entirely — is not a usable answer.
    """
    try:
        body = resp.json()
    except ValueError:
        return None if _looks_like_markup(resp.text) else resp.text
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
        workspace: WorkspaceOverride | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
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
                # Unlike `skills` below, this key is always sent: it states
                # something true of every call, not an override the playground
                # opted into. Compatibility rests on the far side ignoring keys
                # it does not know — a server that has not implemented the
                # budget yet answers exactly as it did before.
                "timeout_s": server_budget_s(self.timeout_s),
            },
        }
        # `is not None`, never a truthiness test. An eval run sends no override
        # at all and its request body must not grow a key; but `skills == {}` is
        # a real instruction — "run this call with no skills" — and `{}` is
        # falsy, so `if workspace.skills:` would drop it and the agent would
        # quietly fall back to its own files, which is the opposite of what was
        # asked for.
        #
        # The agent server is expected to apply this to THIS call only, never to
        # persist it, and to treat it as replacing its skill directory rather
        # than patching it.
        if workspace is not None and workspace.skills is not None:
            payload["metadata"]["skills"] = workspace.skills
        return payload

    async def call(
        self, question: str, correlation_id: str, user_id: str,
        tags: list[str] | None = None,
        workspace: WorkspaceOverride | None = None,
    ) -> AgentResponse:
        payload = self.build_payload(
            question, correlation_id, user_id, tags, workspace
        )
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
