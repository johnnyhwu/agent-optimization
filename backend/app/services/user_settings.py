"""A developer's saved defaults, and the one place they are laid over the
deployment's.

**This module is off the path a run executes through, and must stay off it.**
`run_config.defaults()` looks like the obvious home for the overlay — it is the
function the "Run eval" dialog is prefilled from — but `resolve()` calls it too,
and `resolve()` decides what a run actually does. The same trap is set twice
more: `hyperparams.resolve_algorithm()` calls `algorithm_defaults()`, and the
optimizer engine calls `StopPolicy.from_config()` once per step. Teaching any of
those three about the caller would make the same POST produce different runs for
different people, decided in a file nobody would think to open, and every
existing test would still pass. So the overlay lives here, the two `/defaults`
endpoints call it, and `tests/test_user_settings_isolation.py` fails if that
stops being true.

**Presence, not truthiness.** A key absent from `values` means "no opinion"; a
key present means the user chose that value. `False`, `0`, `""` and `None` are
all choices someone can make — `diagnosis_enabled=False`, `early_stop_patience=0`
("never stop early"), `early_stop_target_score=None` ("aim at nothing"). Every
one of them vanishes under `if value:`; three vanish under `if value is not
None:`. `hyperparams.py` and `stopping.py` were each rewritten to remove exactly
this bug, and their docstrings say so.

That last one is the subtle one. `stopping._number` reads `None` as "not set,
use the environment", which is right for a *run's* stored config and wrong for a
*user's* default — a user whose deployment aims at 0.9 would have no way to say
"don't aim at anything". So the environment is resolved into a plain dictionary
first and the user's values are laid over it by key presence second; the user's
values never pass through `StopPolicy.from_config`.

**Reading is forgiving, writing is not.** A value that was legal when it was
stored can stop being legal when a bound moves or a key is retired. Saving
refuses such a value with a 400; loading drops it, reports it, and carries on —
because this is read by an endpoint that every page in the product loads, and a
stale preference must cost the user one field rather than the whole screen.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import settings_catalog as catalog
from app.auth import normalize_subject
from app.config import settings
from app.models import UserSettings
from app.optimizer import hyperparams, stopping
from app.services import run_config

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Stored:
    """One user's row, or the absence of one."""

    values: dict = field(default_factory=dict)
    system_at_set: dict = field(default_factory=dict)
    secrets: dict = field(default_factory=dict)
    seen_keys: tuple[str, ...] = ()


EMPTY = Stored()


def settings_key(subject: str | None) -> str:
    """The row key for a subject.

    `normalize_subject`, the same casefold every share is written with. Two
    spellings of one username must not be two people with two sets of defaults,
    one of which looks empty for no visible reason.
    """
    return normalize_subject(subject)


# --- Validation -------------------------------------------------------------

class _Rejected(ValueError):
    pass


def _coerce(spec: catalog.SettingSpec, value: Any) -> Any:
    """One stored value as the forms and the engine expect it, or a rejection.

    JSONB round trips are loose — a float written as `90.0` comes back as `90` —
    so the catalogue's declared kind is the type witness, the same role the
    default plays in `hyperparams._coerce`.
    """
    if value is None:
        if spec.optional:
            return None
        raise _Rejected(f"{spec.key} has no 'off' setting")

    if spec.kind == "bool":
        if not isinstance(value, bool):
            raise _Rejected(f"{spec.key} must be true or false")
        return value

    # `bool` is an `int` in Python, and letting True through as 1 here would make
    # a checkbox and a number field interchangeable in storage.
    if isinstance(value, bool):
        raise _Rejected(f"{spec.key} is not a switch")

    if spec.kind == "text":
        if not isinstance(value, str):
            raise _Rejected(f"{spec.key} must be text")
        return value

    if not isinstance(value, (int, float)):
        raise _Rejected(f"{spec.key} must be a number")

    if spec.kind == "int":
        if isinstance(value, float) and not value.is_integer():
            raise _Rejected(f"{spec.key} must be a whole number")
        number: Any = int(value)
    else:
        number = float(value)

    if spec.minimum is not None and number < spec.minimum:
        raise _Rejected(f"{spec.key} must be at least {spec.minimum}")
    if spec.maximum is not None and number > spec.maximum:
        raise _Rejected(f"{spec.key} must be at most {spec.maximum}")
    return number


def clean(stored: dict | None) -> tuple[dict, list[str]]:
    """Stored values, as far as they can still be trusted.

    Returns what survives and the keys that did not. Nothing raises: see the
    module docstring on why loading forgives what saving refuses.
    """
    kept: dict[str, Any] = {}
    invalid: list[str] = []
    for key, value in (stored or {}).items():
        spec = catalog.BY_KEY.get(key)
        if spec is None or spec.kind == "secret":
            invalid.append(key)
            continue
        try:
            kept[key] = _coerce(spec, value)
        except _Rejected:
            invalid.append(key)
    return kept, sorted(invalid)


def validate_for_write(values: dict | None) -> dict:
    """The same check, as a refusal. Raises `ValueError` on the first problem."""
    out: dict[str, Any] = {}
    for key, value in (values or {}).items():
        spec = catalog.BY_KEY.get(key)
        if spec is None:
            raise ValueError(f"not a settable key: {key}")
        if spec.kind == "secret":
            # Credentials have their own endpoint so they never share a request
            # body with values that are safe to log.
            raise ValueError(f"{key} is a credential; use the credentials endpoint")
        out[key] = _coerce(spec, value)
    return out


# --- The overlay ------------------------------------------------------------

def _overlay(system: dict, stored: dict) -> dict:
    """The deployment's values, with the user's laid over them.

    Membership, never truthiness — see the module docstring. `stored` is
    restricted to keys the environment already has, so a retired preference
    cannot reintroduce a field the forms no longer know about.
    """
    return {**system, **{k: v for k, v in stored.items() if k in system}}


def run_defaults(stored: dict | None) -> dict:
    """What the "Run eval" dialog and the playground open with."""
    values, _ = clean(stored)
    return _overlay(run_config.defaults(), values)


def _optimization_system_defaults() -> dict:
    """Everything the Optimize wizard prefills, resolved to the environment.

    Assembled the same way `optimization_defaults` in the router assembles it, so
    the two cannot disagree about what an untouched wizard would do.
    """
    system = dict(run_config.defaults())
    system["optimizer_model"] = settings.optimizer_model
    system["num_epochs"] = settings.optimizer_num_epochs
    system["batch_size"] = settings.optimizer_batch_size
    system.update(hyperparams.algorithm_defaults())
    # Resolved to plain numbers *before* the overlay, so that a user's `None`
    # for the target score reads as "off" rather than being handed to
    # `_number`, which would read it as "not set" and hand back the
    # environment's value.
    system.update(stopping.StopPolicy.from_config({}).as_dict())
    return system


def optimization_defaults(stored: dict | None) -> dict:
    """What the Optimize wizard opens with."""
    values, _ = clean(stored)
    return _overlay(_optimization_system_defaults(), values)


# --- Storage ----------------------------------------------------------------

async def load(session: AsyncSession, subject: str) -> Stored:
    """One user's row. Never creates one — see `ensure_row` for why that matters."""
    row = await session.get(UserSettings, settings_key(subject))
    if row is None:
        return EMPTY
    return Stored(
        values=dict(row.values or {}),
        system_at_set=dict(row.system_at_set or {}),
        secrets=dict(row.secrets or {}),
        seen_keys=tuple(row.seen_keys or ()),
    )


async def ensure_row(
    session: AsyncSession, subject: str, *, seen: Iterable[str] | None = None
) -> None:
    """Create this user's row if they do not have one, with a seen-key baseline.

    Only the settings page calls this, and that is the whole design of the "new
    setting available" hint. "New" has to mean "you have not seen this", not "you
    have not set this": a first visit that badged every unset key would show
    twenty-five badges to somebody who has never expressed an opinion about
    anything, which is the same as having no hint at all. So the row is created
    on that first visit carrying every key that exists at the time, and only keys
    introduced afterwards are ever new.

    `ON CONFLICT DO NOTHING` because two browser tabs opening the page together
    both reach here, and the second one must be a no-op rather than an error.
    """
    baseline = list(seen) if seen is not None else [s.key for s in catalog.CATALOG]
    await session.execute(
        pg_insert(UserSettings)
        .values(subject=settings_key(subject), seen_keys=baseline)
        .on_conflict_do_nothing(index_elements=["subject"])
    )
    await session.commit()


async def _row(session: AsyncSession, subject: str) -> UserSettings:
    await ensure_row(session, subject)
    row = await session.get(UserSettings, settings_key(subject))
    assert row is not None  # just created if it was missing
    return row


async def save_values(session: AsyncSession, subject: str, values: dict) -> None:
    """Replace this user's overrides.

    The whole set, not a patch: the settings page sends what its form says, and a
    key that is gone from that is a key the user cleared. `system_at_set` is
    rewritten alongside, because what matters later is what the environment said
    when *this* value was chosen.
    """
    row = await _row(session, subject)
    row.values = values
    row.system_at_set = {
        key: catalog.system_value(catalog.BY_KEY[key])
        for key in values
        if key in catalog.BY_KEY
    }
    await session.commit()


async def save_secret(
    session: AsyncSession, subject: str, key: str, entry: dict
) -> None:
    row = await _row(session, subject)
    # Reassigned rather than mutated: SQLAlchemy does not track in-place changes
    # to a JSONB dict, so `row.secrets[key] = ...` would not be written.
    row.secrets = {**(row.secrets or {}), key: entry}
    await session.commit()


async def delete_secret(session: AsyncSession, subject: str, key: str) -> None:
    row = await _row(session, subject)
    row.secrets = {k: v for k, v in (row.secrets or {}).items() if k != key}
    await session.commit()


async def mark_seen(session: AsyncSession, subject: str, keys: Iterable[str]) -> None:
    row = await _row(session, subject)
    row.seen_keys = sorted(set(row.seen_keys or ()) | set(keys))
    await session.commit()


# --- What the settings page needs to say ------------------------------------

def unseen_keys(stored: Stored) -> list[str]:
    """Settings introduced since this user last looked."""
    seen = set(stored.seen_keys)
    return sorted(s.key for s in catalog.CATALOG if s.key not in seen)


def drifted_keys(stored: Stored) -> list[dict]:
    """Overrides whose deployment value has changed underneath them.

    The mirror image of the new-key hint, and the one that actually breaks
    things: an admin repoints `LLM_BASE_URL` because the old host is gone, and
    every user who overrode it keeps talking to the dead one with nothing on
    screen to explain why only they are broken.
    """
    out = []
    for key in sorted(stored.values):
        spec = catalog.BY_KEY.get(key)
        if spec is None or key not in stored.system_at_set:
            continue
        now = catalog.system_value(spec)
        was = stored.system_at_set[key]
        if was != now:
            out.append({"key": key, "was": was, "now": now})
    return out


# --- What the two /defaults endpoints call ----------------------------------

async def _stored_values(session: AsyncSession, subject: str) -> dict:
    """This user's overrides, or none of them if the read fails.

    Every page in the product loads a defaults endpoint. A database hiccup here
    must cost the user their preferences for one request, not the screen.
    """
    try:
        return (await load(session, subject)).values
    except Exception:  # noqa: BLE001
        log.warning("could not read personal defaults; using the environment's", exc_info=True)
        return {}


async def effective_run_defaults(session: AsyncSession, subject: str) -> dict:
    return run_defaults(await _stored_values(session, subject))


async def effective_optimization_defaults(session: AsyncSession, subject: str) -> dict:
    return optimization_defaults(await _stored_values(session, subject))


async def stored_secrets(session: AsyncSession, subject: str) -> dict:
    """The encrypted entries, for the injection path only. Never rendered."""
    try:
        return (await load(session, subject)).secrets
    except Exception:  # noqa: BLE001
        log.warning("could not read personal credentials", exc_info=True)
        return {}
