"""A developer's saved credentials: encrypted, never returned, bound to one
endpoint.

`runs.secrets` stores a run's credentials as plaintext JSONB, and that is a
defensible trade: one afternoon's key, typed by the person starting the run,
readable only by something that already holds the database password. The
property being relied on is structural rather than cryptographic — no response
model reads that column, so "credentials never leave the server" cannot be
broken by forgetting to maintain a list.

A *saved default* is the same value with a different shape of risk. It has no
expiry and it is reachable by every run its owner starts, so it is encrypted at
rest as well as structurally unreadable. Four rules, each protecting against a
failure the others do not:

**Fail closed.** No `SETTINGS_SECRET_KEY`, no feature — never a plaintext
fallback. A fallback here would be invisible at the moment it mattered and
permanent afterwards.

**Endpoint binding.** A credential is only ever sent to the endpoint it was
stored against. Without that rule, editing the LLM base URL in the run dialog
would hand the user's key to whatever address was typed. `routers/runs.py`
already carries this rule for credentials borrowed from an earlier run, and its
docstring is where the reasoning was first written down; this is the same rule
applied to a longer-lived store. The consequence is a deliberate piece of
friction — change the endpoint and the credential has to be entered again — and
that is the feature, not a rough edge.

**Not under fake auth.** `AUTH_MODE=fake` decides identity from a header the
caller sets. Keying long-lived credentials on that is a hole, so the store is
refused at both ends rather than merely hidden in the UI: a deployment switched
from keycloak to fake still has the rows, and must stop using them.

**Decryption failure is not an outage.** A rotated or lost key degrades to
"there is no stored credential" and says so on the settings page. Raising would
take out an endpoint that every page in the product loads.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken

from app import settings_catalog as catalog
from app.config import settings

log = logging.getLogger(__name__)


class SecretsUnavailable(RuntimeError):
    """Asked to store a credential where storing one is switched off."""


def available() -> bool:
    """Whether this deployment stores personal credentials at all."""
    if not (settings.settings_secret_key or "").strip():
        return False
    # See the module docstring: fake identity is a header, not a login.
    return settings.auth_mode != "fake"


def unavailable_reason() -> str | None:
    """Why the credential fields are switched off, in words for the page."""
    if not (settings.settings_secret_key or "").strip():
        return "This deployment has no SETTINGS_SECRET_KEY, so credentials are not stored."
    if settings.auth_mode == "fake":
        return (
            "Demo sign-in identifies you by a header anyone can set, so credentials "
            "are not stored in this mode."
        )
    return None


def _cipher() -> Fernet:
    return Fernet((settings.settings_secret_key or "").strip().encode())


def entry(key: str, plaintext: str, *, endpoint: str) -> dict:
    """One credential as the row stores it.

    `endpoint` is recorded beside the ciphertext rather than looked up later:
    the binding has to be to the address the user was looking at when they typed
    the key, not to whatever the environment says today.
    """
    if key not in catalog.SECRET_KEYS:
        raise ValueError(f"not a credential: {key}")
    if not available():
        raise SecretsUnavailable(unavailable_reason() or "credential storage is off")
    return {
        "ciphertext": _cipher().encrypt(plaintext.encode()).decode(),
        "endpoint": (endpoint or "").strip(),
        # Second precision: this is shown on a page, and a microsecond field
        # would be noise.
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }


def _plaintext(stored_entry: object) -> str | None:
    """The credential behind one row entry, or None if it cannot be read."""
    if not isinstance(stored_entry, dict):
        return None
    ciphertext = stored_entry.get("ciphertext")
    if not isinstance(ciphertext, str) or not ciphertext:
        return None
    try:
        return _cipher().decrypt(ciphertext.encode()).decode()
    except (InvalidToken, ValueError, TypeError):
        # Rotated key, restored backup, hand-edited row. The user has to enter
        # it again; nothing else about the request is affected.
        log.warning("a stored credential could not be decrypted; treating it as absent")
        return None


def inject(stored: dict | None, config: dict | None, provided: dict | None) -> dict:
    """The credentials a request executes with.

    Precedence, highest first: what the caller typed into this request, then the
    caller's saved default *for this endpoint*. Nothing else — a saved default
    for a different endpoint is not a fallback, it is a credential for somewhere
    else.
    """
    resolved = {k: v for k, v in (provided or {}).items() if v}
    if not available():
        return resolved

    stored = stored or {}
    config = config or {}
    for key, endpoint_key in catalog.SECRET_ENDPOINTS.items():
        if resolved.get(key):
            continue  # typed into this request
        stored_entry = stored.get(key)
        if not isinstance(stored_entry, dict):
            continue
        want = str(config.get(endpoint_key) or "").strip()
        have = str(stored_entry.get("endpoint") or "").strip()
        if want != have:
            continue  # a credential for a different address is not this one
        plaintext = _plaintext(stored_entry)
        if plaintext:
            resolved[key] = plaintext
    return resolved


def public_view(stored: dict | None) -> dict:
    """What the browser is told about the stored credentials.

    Whether one is set, which endpoint it belongs to, when it was written, and
    whether it can still be read. Deliberately not the last four characters: a
    fingerprint of a credential is still a fingerprint of a credential, and it
    would be handed to anyone who can read the page.
    """
    stored = stored or {}
    view = {}
    for key in catalog.SECRET_KEYS:
        stored_entry = stored.get(key)
        is_set = isinstance(stored_entry, dict) and bool(stored_entry.get("ciphertext"))
        view[key] = {
            "set": is_set,
            "endpoint": (stored_entry or {}).get("endpoint", "") if is_set else "",
            "updated_at": (stored_entry or {}).get("updated_at") if is_set else None,
            # False means "set, but this deployment can no longer decrypt it" —
            # the page has to say so, because otherwise the credential silently
            # stops being used and nothing explains why.
            "readable": bool(is_set and _plaintext(stored_entry) is not None),
        }
    return view
