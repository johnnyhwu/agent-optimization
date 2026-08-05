"""The judge prompt an eval set grades with, and the rules around it.

**Why the prompt lives on the eval set and not in the run config.** Everything
else in a run config answers "where do I connect and how fast do I go" — that is
the caller's business, which is why a viewer may set it (§6.16). The judge prompt
answers "what counts as correct", and that is a property of the question set: if
every caller could bring their own, two runs of the same eval set would produce
pass rates that cannot be compared, and comparing them is what the whole middle
tier exists to do. Putting it on the eval set also means the existing owner-only
guard covers it — no per-field permission rule had to be invented.

**Two directions, on purpose.** The eval set holds a *live* setting: `NULL` means
"use whatever the code's default is today", so improving `DEFAULT_SYSTEM` below
benefits every set that never overrode it. A run holds a *frozen* one: `resolve`
writes the full text into `runs.config`, so a finished run always says exactly
what it graded with, even after the set has moved on. The two rules look
contradictory and are both deliberate — a setting should follow the product, a
historical record must not.

The fingerprint is the cheap half of versioning: two runs with the same
fingerprint were graded by the same words, and that is the only question anyone
actually asks of a prompt's history ("is this pass rate comparable to that one?").
"""
from __future__ import annotations

import hashlib

from app.integrations.real.prompts import (
    DEFAULT_JUDGE_SYSTEM,
    DEFAULT_JUDGE_USER,
    JUDGE_PLACEHOLDERS,
    missing_placeholders,
)


def effective(
    system_prompt: str | None, user_prompt: str | None
) -> tuple[str, str]:
    """The prompt that will actually be sent: the set's override, or the default.

    Blank counts as unset, matching `integrations._get` — a textarea someone
    emptied means "back to the default", not "grade with no instructions".
    """
    system = (system_prompt or "").strip() or DEFAULT_JUDGE_SYSTEM
    user = (user_prompt or "").strip() or DEFAULT_JUDGE_USER
    return system, user


def is_default(system_prompt: str | None, user_prompt: str | None) -> bool:
    return effective(system_prompt, user_prompt) == (
        DEFAULT_JUDGE_SYSTEM,
        DEFAULT_JUDGE_USER,
    )


def fingerprint(system_prompt: str | None, user_prompt: str | None) -> str:
    """A short, stable id for one pair of prompts.

    Eight hex characters: enough that two prompts in one eval set will not
    collide, short enough to sit in a chip on every row of the run list. It is
    not a secret and not a version number — only an equality test people can
    read.
    """
    system, user = effective(system_prompt, user_prompt)
    digest = hashlib.sha256(f"{system}\x00{user}".encode()).hexdigest()
    return digest[:8]


__all__ = [
    "JUDGE_PLACEHOLDERS",
    "effective",
    "fingerprint",
    "is_default",
    "missing_placeholders",
]
