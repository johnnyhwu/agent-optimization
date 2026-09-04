"""Real AgentClient: an OpenAI-standard chat completions endpoint (§6.2).

The agent server exposes `POST {agent_chat_url}` speaking the OpenAI chat
completions shape — because that is the one endpoint a domain-agent team almost
certainly already has. Everything this platform needs beyond it rides in a
single vendor namespace, `skill_studio`, alongside `messages`:

    {"model": "default",
     "messages": [{"role": "user", "content": "<the question>"}],
     "stream": false,
     "skill_studio": {"skills": {...}, "timeout_s": 115.0, "trace_data": {...}}}

**Why a top-level vendor key and not `metadata`.** OpenAI's own `metadata` is
specified as at most 16 string→string pairs of 512 characters; a skill file set
does not fit and a strict gateway rejects it outright. A namespaced sibling of
`messages` is the conventional escape hatch — it is exactly what the OpenAI
SDKs' `extra_body` flattens into — and keeping everything under one key means a
gateway that filters unknown fields has one thing to allow rather than three.

`skills` is the complete file set for one call, never a patch: it *replaces* the
agent's directory for that call, which is the only shape that can express
deleting a file (see `optimizer/adapter.py`). An eval run sends no override at
all, so its request body never grows the key.

Every call also carries `skill_studio.timeout_s`, the budget the agent server
should give itself for this one question — see `server_budget_s` for why it is
not simply this client's own timeout.

The correlation mechanism (§6.2 / §6.7) is the whole point of this client: the
platform mints a correlation_id per question and puts it in
`skill_studio.trace_data.trace_id` (and reuses it as `session_id`, since each
question is its own Langfuse session); the agent server applies it as its
Langfuse trace id, which is how the trace is found again later.
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from app.config import settings
from app.integrations.base import AgentResponse, WorkspaceOverride
from app.integrations.real.agent_auth import auth_headers, redact

# The vendor namespace every platform-specific field lives under. One name, in
# one place, because it appears in the payload, in the docs and in the probe.
VENDOR_KEY = "skill_studio"

# Sent on every call and ignored by every agent we expect to talk to. It is here
# because `model` is *required* by the OpenAI request schema: a team fronting
# their agent with LiteLLM, vLLM or an internal gateway gets a 422 without it,
# and that error names a field nobody chose to omit. One constant buys immunity
# from a whole class of confusing rejections.
DEFAULT_MODEL = "default"


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
    server enforces its own limit on how long one call may run; without being
    told ours it uses a built-in default, which is why raising the platform's
    timeout past that default used to change nothing. Sending it solves that —
    but sending the *same* value would make "who gives up first" a race, and the
    two outcomes read very differently: the server timing out is an answer with
    a reason in it, while we timing out is a dropped connection. So the server
    is asked to finish first, by `margin_s`.

    Never less than half of `timeout_s`: a margin wider than the timeout itself
    (a 3s timeout against the default 5s margin) must not become a zero or
    negative budget.
    """
    return round(max(timeout_s - max(margin_s, 0.0), timeout_s / 2), 3)


def _looks_like_markup(text: str) -> bool:
    """Does this body open as HTML/XML rather than as an answer?

    The one non-JSON body that must never be passed through. A proxy, gateway
    or web framework answering 200 with an error page is not an agent
    answering, and if it were accepted the judge would grade the markup and
    record a confident wrong verdict against the agent.

    Keyed on how the body *opens*, not on containing a tag anywhere: an answer
    may perfectly well discuss `<b>`.
    """
    return text.lstrip()[:1] == "<"


def _content_text(content: Any) -> str | None:
    """The assistant's text, from either shape `content` is allowed to take.

    A plain string is the common case. The newer content-parts array is what
    reasoning and multimodal models return, and dropping it would fail those
    agents for a reason that has nothing to do with this platform — so the text
    parts are concatenated and everything else in the array ignored.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            p.get("text")
            for p in content
            if isinstance(p, dict) and p.get("type") == "text" and isinstance(p.get("text"), str)
        ]
        return "".join(parts) if parts else None
    return None


def _first_choice(body: Any) -> dict | None:
    if not isinstance(body, dict):
        return None
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    return choices[0] if isinstance(choices[0], dict) else None


def _extract_text(resp: httpx.Response) -> str | None:
    """The answer out of a chat completions body: `choices[0].message.content`.

    Strict about the envelope on purpose. The previous protocol also accepted a
    bare JSON string and a `text/plain` body, because it had no standard shape
    to point at; this one does, and every extra accepted shape is a way for a
    gateway's stray response to be graded as an answer.
    """
    try:
        body = resp.json()
    except ValueError:
        # Not JSON at all. The only thing worth rejecting outright is markup —
        # everything else is at least plausibly an agent that answered oddly,
        # and the caller reports it with the body quoted.
        return None
    choice = _first_choice(body)
    if choice is None:
        return None
    message = choice.get("message")
    if not isinstance(message, dict):
        return None
    return _content_text(message.get("content"))


def _extract_error(resp: httpx.Response) -> str:
    """What to show a developer about a failed call: the agent's own words.

    An OpenAI-shaped error carries a written sentence in `error.message`;
    showing that instead of the raw envelope is the difference between "context
    length exceeded" and four lines of JSON. Anything else falls back to the
    beginning of the body, which is what the old protocol always did.
    """
    try:
        body = resp.json()
    except ValueError:
        return f"agent server returned {resp.status_code}: {resp.text[:500]}"
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and isinstance(err.get("message"), str):
            return f"agent server returned {resp.status_code}: {err['message'][:500]}"
        if isinstance(err, str):
            return f"agent server returned {resp.status_code}: {err[:500]}"
    return f"agent server returned {resp.status_code}: {resp.text[:500]}"


def build_payload(
    question: str,
    correlation_id: str,
    user_id: str,
    tags: list[str] | None,
    timeout_s: float,
    workspace: WorkspaceOverride | None = None,
) -> dict[str, Any]:
    """The request body for one question.

    A module-level function rather than a method because the connection probe
    (`services/agent_probe.py`) builds the very same body and shows it to the
    developer: two constructions of "what we send" would eventually disagree,
    and the one on screen is the one they would trust.
    """
    payload: dict[str, Any] = {
        # Required by the OpenAI schema; agents are told they may ignore it.
        "model": DEFAULT_MODEL,
        # One user message, never a system message: a question is the whole
        # input, and the agent's own prompt belongs to the agent. The list
        # shape is what makes multi-turn eval expressible later without
        # another protocol change.
        "messages": [{"role": "user", "content": question}],
        # Always false. Accumulating a stream would work, but it makes every
        # failure mode harder to report and buys nothing for a batch runner.
        "stream": False,
        VENDOR_KEY: {
            # Unlike `skills` below, this key is always sent: it states
            # something true of every call, not an override the playground
            # opted into. Compatibility rests on the far side ignoring keys it
            # does not know — a server that has not implemented the budget yet
            # answers exactly as it did before.
            "timeout_s": server_budget_s(timeout_s),
            "trace_data": {
                "trace_id": correlation_id,
                # Same value as trace_id: each question is its own correlation
                # unit, so it is also its own Langfuse session.
                "session_id": correlation_id,
                "user_id": user_id,
                "tags": tags or [],
            },
        },
    }
    # `is not None`, never a truthiness test. An eval run sends no override at
    # all and its request body must not grow a key; but `skills == {}` is a real
    # instruction — "run this call with no skills" — and `{}` is falsy, so
    # `if workspace.skills:` would drop it and the agent would quietly fall back
    # to its own files, which is the opposite of what was asked for.
    #
    # The agent server is expected to apply this to THIS call only, never to
    # persist it, and to treat it as replacing its skill directory rather than
    # patching it.
    if workspace is not None and workspace.skills is not None:
        payload[VENDOR_KEY]["skills"] = workspace.skills
    return payload


class HttpAgentClient:
    """POST a question to the agent's chat completions endpoint, return its answer."""

    def __init__(
        self,
        chat_url: str | None = None,
        timeout_s: float | None = None,
        api_key: str | None = None,
        auth_header: str | None = None,
    ) -> None:
        self.chat_url = (chat_url or settings.agent_chat_url).strip().rstrip("/")
        if not self.chat_url:
            raise RuntimeError(
                "AGENT_IMPL=real but no agent chat endpoint was given — set it in "
                "the run config, or via AGENT_CHAT_URL "
                "(e.g. http://agent-host:8080/v1/chat/completions)."
            )
        self.timeout_s = timeout_s or settings.agent_timeout_s
        # Both optional. With no key this client sends exactly the headers it
        # sent before authentication existed — the agent contract does not
        # require any, and most servers this talks to ask for none.
        self.api_key = (api_key or settings.agent_api_key or "").strip()
        self.auth_header = (auth_header or settings.agent_auth_header or "").strip()

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            **auth_headers(self.api_key, self.auth_header),
        }

    def _safe(self, text: str) -> str:
        """Whatever the agent said, with our credential taken back out.

        Every error path below quotes the response body, and those quotes end up
        in run records and on screen. A gateway that echoes request headers into
        its error body is the case this covers.
        """
        return redact(text, self.api_key)

    def build_payload(
        self, question: str, correlation_id: str, user_id: str,
        tags: list[str] | None,
        workspace: WorkspaceOverride | None = None,
    ) -> dict[str, Any]:
        return build_payload(
            question, correlation_id, user_id, tags, self.timeout_s, workspace
        )

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
                self.chat_url, json=payload, headers=self._headers()
            )

        latency_ms = int((time.monotonic() - started) * 1000)

        # Let 5xx/timeouts raise so the orchestrator's retry policy sees them;
        # a 4xx is a request problem and will fail identically on every retry.
        if resp.status_code >= 500:
            raise AgentHttpError(self._safe(_extract_error(resp)))
        if resp.status_code >= 400:
            return AgentResponse(
                response="", correlation_id=correlation_id, failed=True,
                error=self._safe(_extract_error(resp)), latency_ms=latency_ms,
            )

        if _looks_like_markup(resp.text):
            return AgentResponse(
                response="", correlation_id=correlation_id, failed=True,
                error=(
                    "the agent server answered 200 with markup, not a chat "
                    f"completion: {self._safe(resp.text[:500])}"
                ),
                latency_ms=latency_ms,
            )

        try:
            body = resp.json()
        except ValueError:
            body = None
        text = _extract_text(resp) if body is not None else None
        if text is None:
            return AgentResponse(
                response="", correlation_id=correlation_id, failed=True,
                error=(
                    "the response was not a chat completion carrying a text "
                    f"answer: {self._safe(resp.text[:500])}"
                ),
                latency_ms=latency_ms,
            )

        text = text.strip()
        if not text:
            # An empty answer is a failure, not a wrong answer: judging "" would
            # produce a meaningless incorrect verdict and hide the real problem.
            return AgentResponse(
                response="", correlation_id=correlation_id, failed=True,
                error="the agent server returned an empty answer.",
                latency_ms=latency_ms,
            )

        choice = _first_choice(body) or {}
        usage = body.get("usage") if isinstance(body, dict) else None
        return AgentResponse(
            response=text,
            correlation_id=correlation_id,
            latency_ms=latency_ms,
            truncated=choice.get("finish_reason") == "length",
            usage=usage if isinstance(usage, dict) else None,
        )
