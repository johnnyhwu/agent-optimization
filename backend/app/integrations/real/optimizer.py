"""Real OptimizerClient: the model that writes skill edits, over any OpenAI endpoint.

**Synchronous, unlike every other real seam here, and deliberately so.** The
vendored SkillOpt modules that call it (`reflect`, `aggregate`, `clip`) are
synchronous and parallelise themselves with a `ThreadPoolExecutor`, so the engine
runs that entire stage inside `asyncio.to_thread`. A coroutine would have to be
driven from those worker threads, which means either re-entering the event loop
from a thread or unpicking upstream's own parallelism — the two things this
arrangement exists to avoid. See `app/optimizer/VENDORED.md`.

`openai` is already a dependency for `AsyncOpenAI`; this uses its synchronous
sibling, so no new package arrives for the sake of one seam.

The signature is upstream's `chat_optimizer` exactly, which is what lets the
vendored files differ from upstream by a single import line each.
"""
from __future__ import annotations

import logging
import time

from openai import OpenAI

from app.config import settings

log = logging.getLogger(__name__)

_clients: dict[tuple, OpenAI] = {}


def get_sync_client(
    base_url: str | None = None,
    api_key: str | None = None,
    timeout_s: float | None = None,
) -> OpenAI:
    """A pooled synchronous client for one endpoint. Blank arguments fall back to env.

    Pooled for the same reason `real/llm.py` pools its async ones: a reflect
    stage opens `analyst_workers` calls at once, and building a client per call
    would open a fresh connection pool per call with it.
    """
    url = base_url or settings.llm_base_url
    if not url:
        raise RuntimeError(
            "OPTIMIZER_IMPL=real but no LLM base URL was given — set it in the "
            "optimization run's settings, or via LLM_BASE_URL."
        )
    key = api_key or settings.llm_api_key or "not-needed"
    timeout = timeout_s or settings.llm_timeout_s

    cache_key = (url, key, timeout)
    if cache_key not in _clients:
        _clients[cache_key] = OpenAI(
            base_url=url,
            api_key=key,
            timeout=timeout,
            # The retry policy is this client's own (below), so the SDK's would
            # multiply with it — the same choice `real/llm.py` makes.
            max_retries=0,
        )
    return _clients[cache_key]


class LlmOptimizerClient:
    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        client: OpenAI | None = None,
    ) -> None:
        self.model_name = model or settings.optimizer_model
        if not self.model_name:
            raise RuntimeError(
                "OPTIMIZER_IMPL=real but no optimizer model was given — set it in "
                "the optimization run's settings, or via OPTIMIZER_MODEL."
            )
        self._client = client
        self._base_url = base_url
        self._api_key = api_key

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            self._client = get_sync_client(self._base_url, self._api_key)
        return self._client

    def chat_optimizer(
        self,
        system: str,
        user: str,
        max_completion_tokens: int = 16384,
        retries: int = 3,
        stage: str = "optimizer",
        timeout: int | None = None,
    ) -> tuple[str, dict[str, int]]:
        """One optimizer call. Returns `(text, usage)` exactly as upstream expects.

        Retries here rather than in the SDK because the caller counts on getting
        *something* back: upstream treats an exception as "no patch from this
        minibatch" and carries on, so a transient blip would silently cost a step
        part of its gradient. The text is returned unparsed — upstream's
        `extract_json` owns the parsing, including its tolerance for models that
        wrap JSON in prose.
        """
        last: Exception | None = None
        for attempt in range(max(retries, 0) + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    max_completion_tokens=max_completion_tokens,
                    timeout=timeout,
                )
            except Exception as exc:  # noqa: BLE001 - retried, then reported
                last = exc
                if attempt >= retries:
                    break
                delay = 2.0**attempt
                log.warning(
                    "optimizer call (%s) failed: %s; retrying in %.1fs", stage, exc, delay
                )
                time.sleep(delay)
                continue

            text = (response.choices[0].message.content or "") if response.choices else ""
            usage = {
                "calls": 1,
                "prompt_tokens": getattr(response.usage, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(response.usage, "completion_tokens", 0) or 0,
            }
            return text, usage

        raise last if last is not None else RuntimeError(f"optimizer call ({stage}) failed")
