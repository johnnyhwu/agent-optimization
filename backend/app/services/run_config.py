"""The non-secret settings a run is triggered with (§9.2 seams).

One place owns the nine fields and their environment-derived defaults, because
two callers need to agree on them exactly: `GET /run-config/defaults`, which
prefills the "Run eval" dialog, and `trigger_run`, which records what the run
actually used. If those drifted apart the UI would show one thing and the run
would do another.

`resolve` materializes: a field the developer left blank is stored with the
environment's value rather than dropped. That costs a few bytes and buys an
unambiguous history — a blank in a stored config would otherwise be unreadable
after the fact ("was that the env value, or nothing at all?"), and the
environment of today is no witness to what it held when the run started.
"""
from __future__ import annotations

from typing import Any

from app.config import settings
from app.schemas import RunConfig


def defaults() -> dict[str, Any]:
    """Environment-derived defaults for every non-secret run setting."""
    return {
        "agent_base_url": settings.agent_base_url,
        "agent_timeout_s": settings.agent_timeout_s,
        "langfuse_host": settings.langfuse_host,
        "langfuse_public_key": settings.langfuse_public_key,
        "langfuse_timeout_s": settings.langfuse_timeout_s,
        "llm_base_url": settings.llm_base_url,
        "judge_model": settings.judge_model,
        "diagnosis_model": settings.diagnosis_model,
        "concurrency": settings.run_concurrency,
    }


def _supplied(value: Any) -> bool:
    """Did the developer actually give a value?

    Same rule as `integrations.__init__._get`, so what counts as "blank" is the
    same whether a value is being stored or being turned into a client.
    """
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return True


def resolve(config: RunConfig) -> dict[str, Any]:
    """The effective settings for a run: defaults, overlaid with what was given."""
    effective = defaults()
    for key, value in config.model_dump().items():
        if _supplied(value):
            effective[key] = value
    return effective
