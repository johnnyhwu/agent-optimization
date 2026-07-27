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


_client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI:
    """One shared async client (it pools connections internally)."""
    global _client
    if _client is None:
        if not settings.llm_base_url:
            raise RuntimeError(
                "A real LLM seam is enabled but LLM_BASE_URL is empty — set it to "
                "the OpenAI-compatible endpoint."
            )
        _client = AsyncOpenAI(
            base_url=settings.llm_base_url,
            # Endpoints that don't check auth still want a non-empty key.
            api_key=settings.llm_api_key or "not-needed",
            timeout=settings.llm_timeout_s,
            max_retries=0,  # the orchestrator owns the retry policy
        )
    return _client


def reset_client() -> None:
    """Drop the cached client (tests / settings changes)."""
    global _client
    _client = None


def _strip_code_fence(text: str) -> str:
    """Some models wrap JSON in ```json fences despite being asked not to."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    body = stripped.split("\n", 1)[1] if "\n" in stripped else ""
    if body.rstrip().endswith("```"):
        body = body.rstrip()[: -len("```")]
    return body.strip()


async def _complete(model: str, messages: list[dict], json_mode: bool) -> str:
    kwargs: dict = {"model": model, "messages": messages, "temperature": 0}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    completion = await get_client().chat.completions.create(**kwargs)
    return completion.choices[0].message.content or ""


async def complete_json(model: str, messages: list[dict], schema: type[T]) -> T:
    """Call the model and parse its reply into `schema`.

    On a parse/validation failure we give the model exactly one repair attempt,
    handing back its own output and the error. If that also fails we raise —
    a caller must never silently receive a made-up default.
    """
    json_mode = True
    try:
        raw = await _complete(model, messages, json_mode=True)
    except Exception as exc:  # noqa: BLE001
        if "response_format" not in str(exc):
            raise
        json_mode = False
        raw = await _complete(model, messages, json_mode=False)

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
        retry_raw = await _complete(model, repair, json_mode=json_mode)
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
