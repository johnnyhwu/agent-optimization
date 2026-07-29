"""Shared OpenAI-compatible LLM access for the judge and the diagnosis seams.

`base_url` points at whatever OpenAI-compatible endpoint the deployment uses —
OpenAI itself, a gateway, or a self-hosted server.

JSON handling is deliberately conservative. We ask for `response_format:
{"type": "json_object"}` rather than a `json_schema`, because many self-hosted
OpenAI-compatible servers implement the former and not the latter; the reply is
then validated against a Pydantic model on our side. If the endpoint rejects
`response_format` entirely we retry without it — the prompts also state the
required shape in words.
"""
from __future__ import annotations

import json
from typing import TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from app.config import settings

T = TypeVar("T", bound=BaseModel)


class LlmOutputError(RuntimeError):
    """The model replied, but not with the JSON contract we asked for."""


# Keyed by (base_url, api_key, timeout): a run may point at a different endpoint
# than the environment default, and each distinct endpoint gets its own pooled
# client rather than one global that the first caller wins.
_clients: dict[tuple[str, str, float], AsyncOpenAI] = {}


def get_client_for(
    base_url: str | None = None,
    api_key: str | None = None,
    timeout_s: float | None = None,
) -> AsyncOpenAI:
    """A pooled async client for one endpoint. Blank arguments fall back to env."""
    url = base_url or settings.llm_base_url
    if not url:
        raise RuntimeError(
            "A real LLM seam is enabled but no LLM base URL was given — set it in "
            "the run config, or via LLM_BASE_URL."
        )
    # Endpoints that don't check auth still want a non-empty key.
    key = api_key or settings.llm_api_key or "not-needed"
    timeout = timeout_s or settings.llm_timeout_s

    cache_key = (url, key, timeout)
    if cache_key not in _clients:
        _clients[cache_key] = AsyncOpenAI(
            base_url=url,
            api_key=key,
            timeout=timeout,
            max_retries=0,  # the orchestrator owns the retry policy
        )
    return _clients[cache_key]


def get_client() -> AsyncOpenAI:
    """The environment-configured client (preflight and other no-config callers)."""
    return get_client_for()


def reset_client() -> None:
    """Drop the cached clients (tests / settings changes)."""
    _clients.clear()


def _strip_code_fence(text: str) -> str:
    """Some models wrap JSON in ```json fences despite being asked not to."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    body = stripped.split("\n", 1)[1] if "\n" in stripped else ""
    if body.rstrip().endswith("```"):
        body = body.rstrip()[: -len("```")]
    return body.strip()


async def _complete(
    model: str, messages: list[dict], json_mode: bool, client: AsyncOpenAI | None
) -> str:
    kwargs: dict = {"model": model, "messages": messages, "temperature": 0}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    completion = await (client or get_client()).chat.completions.create(**kwargs)
    return completion.choices[0].message.content or ""


async def complete_json(
    model: str,
    messages: list[dict],
    schema: type[T],
    client: AsyncOpenAI | None = None,
) -> T:
    """Call the model and parse its reply into `schema`.

    `client` selects the endpoint (a run may use its own); omitting it uses the
    environment-configured one.

    On a parse/validation failure we give the model exactly one repair attempt,
    handing back its own output and the error. If that also fails we raise —
    a caller must never silently receive a made-up default.
    """
    json_mode = True
    try:
        raw = await _complete(model, messages, json_mode=True, client=client)
    except Exception as exc:  # noqa: BLE001
        if "response_format" not in str(exc):
            raise
        json_mode = False
        raw = await _complete(model, messages, json_mode=False, client=client)

    try:
        return schema.model_validate_json(_strip_code_fence(raw))
    except (ValidationError, ValueError) as first_error:
        repair = messages + [
            {"role": "assistant", "content": raw},
            {
                "role": "user",
                "content": (
                    "That reply did not match the required JSON contract "
                    f"({first_error}). Reply again with ONLY the JSON object, "
                    "no prose and no code fences."
                ),
            },
        ]
        retry_raw = await _complete(model, repair, json_mode=json_mode, client=client)
        try:
            return schema.model_validate_json(_strip_code_fence(retry_raw))
        except (ValidationError, ValueError) as second_error:
            raise LlmOutputError(
                f"{model} did not return valid JSON after a repair attempt: "
                f"{second_error}. Last output: {retry_raw[:500]}"
            ) from second_error


def as_text(value: object) -> str:
    """Render a Langfuse/agent payload (str, dict, list, None) as display text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return str(value)
