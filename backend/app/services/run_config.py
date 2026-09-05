"""The non-secret settings a run is triggered with (§9.2 seams).

One place owns the fields and their environment-derived defaults, because
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
        "agent_chat_url": settings.agent_chat_url,
        "agent_skills_url": settings.agent_skills_url,
        "agent_auth_header": settings.agent_auth_header,
        "agent_timeout_s": settings.agent_timeout_s,
        "langfuse_host": settings.langfuse_host,
        "langfuse_public_key": settings.langfuse_public_key,
        "langfuse_timeout_s": settings.langfuse_timeout_s,
        "llm_base_url": settings.llm_base_url,
        "judge_model": settings.judge_model,
        "diagnosis_model": settings.diagnosis_model,
        "diagnosis_enabled": settings.diagnosis_enabled,
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


# Settings a caller may never choose: they come from the eval set, so whatever
# arrives in the request body for them is discarded. Keeping the list here means
# `resolve` cannot forget one — the overwrite below is unconditional.
EVAL_SET_OWNED = ("judge_system_prompt", "judge_user_prompt", "judge_prompt_fingerprint")


def resolve(
    config: RunConfig, judge_prompt: tuple[str, str, str] | None = None
) -> dict[str, Any]:
    """The effective settings for a run: defaults, overlaid with what was given.

    `judge_prompt` is `(system, user, fingerprint)` from the eval set, and it
    **wins over anything the caller sent**. That is the entire enforcement of
    "only an owner decides how answers are graded": the endpoint is still open to
    viewers (§6.16 — anyone may run an eval), the three fields are simply not
    theirs to set. Expressed as an overwrite rather than a 403 on purpose, since
    there is nothing for a caller to correct and nothing to explain.

    It is stored as full text, not as a reference to the eval set, for the same
    reason every other field is materialized: the set can be edited tomorrow, and
    a finished run has to keep saying what it actually graded with.
    """
    # Seeded blank so the stored config still lists every field of `RunConfig`,
    # complete-record rule and all — `defaults()` cannot supply these, since they
    # come from an eval set rather than from the environment. Blank reads the
    # same way it does everywhere else here: fall back to the shipped default.
    effective = {key: "" for key in EVAL_SET_OWNED}
    effective.update(defaults())
    for key, value in config.model_dump().items():
        if key in EVAL_SET_OWNED:
            continue
        if _supplied(value):
            effective[key] = value

    if judge_prompt is not None:
        system, user, fingerprint = judge_prompt
        effective["judge_system_prompt"] = system
        effective["judge_user_prompt"] = user
        effective["judge_prompt_fingerprint"] = fingerprint
    return effective
