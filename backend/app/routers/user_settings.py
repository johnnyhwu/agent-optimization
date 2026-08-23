"""The settings page: one developer's own defaults for every form in the product.

Five endpoints, and the shape of them follows from two properties.

**Credentials go in and never come out.** `PUT /user-settings/secrets/{key}` is
separate from `PUT /user-settings` rather than a field inside it, so a credential
never shares a request body with values that are safe to log, and the read
endpoint answers "is one set" rather than "here it is". The values behind them
only ever leave this process towards the endpoint they were stored against —
see `services/user_secrets.py`.

**The row is created here and nowhere else.** `GET /user-settings` is the first
thing the settings page calls, and creating the row at that moment — carrying
every setting that exists right then — is what makes the "new setting available"
hint mean anything. Creating it on a defaults read instead would be a write on a
path every page in the product hits, and would give a brand-new user a page full
of badges for settings they have never had an opinion about.

Everything here is per-subject and needs no role check: `eval_set_roles` governs
who may read an eval set, and these are nobody's business but their owner's.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app import settings_catalog as catalog
from app.auth import current_subject
from app.db import get_session
from app.schemas import SeenIn, UserSecretIn, UserSettingsIn
from app.services import user_secrets, user_settings

router = APIRouter(prefix="/user-settings", tags=["user-settings"])


@router.get("")
async def get_user_settings(
    session: AsyncSession = Depends(get_session),
    subject: str = Depends(current_subject),
):
    """Everything the settings page draws itself from.

    `system` is what this deployment would use, which the page shows as the
    placeholder in each empty field — the empty field *is* the "I have no
    opinion" state, so there is nothing else to mark and no reset button to add.
    """
    await user_settings.ensure_row(session, subject)
    stored = await user_settings.load(session, subject)
    values, invalid = user_settings.clean(stored.values)
    return {
        "catalog": catalog.as_json(),
        "groups": [
            {"id": gid, "label": label, "description": description}
            for gid, label, description in catalog.GROUPS
        ],
        "system": catalog.system_defaults(),
        "values": values,
        # Stored preferences that are no longer legal — a bound moved, or a key
        # was retired. Reported rather than raised, and reported rather than
        # silently dropped: the user chose them, and they have stopped working.
        "invalid": invalid,
        "unseen": user_settings.unseen_keys(stored),
        "drifted": user_settings.drifted_keys(stored),
        "secrets": user_secrets.public_view(stored.secrets),
        "secrets_available": user_secrets.available(),
        "secrets_unavailable_reason": user_secrets.unavailable_reason(),
    }


@router.get("/status")
async def get_status(
    session: AsyncSession = Depends(get_session),
    subject: str = Depends(current_subject),
):
    """Two counts, for the dot on the user menu.

    Its own endpoint because it is called from every page and the full payload
    above is not: a badge is not worth shipping the whole catalogue for.
    """
    stored = await user_settings.load(session, subject)
    if stored == user_settings.EMPTY:
        # No row means this user has never opened the settings page. They have
        # no baseline to measure "new" against and no overrides to have drifted,
        # so there is nothing to point at — every key would count as unseen and
        # the dot would be on for everybody, permanently.
        return {"unseen": 0, "drifted": 0}
    return {
        "unseen": len(user_settings.unseen_keys(stored)),
        "drifted": len(user_settings.drifted_keys(stored)),
    }


@router.put("")
async def put_user_settings(
    body: UserSettingsIn,
    session: AsyncSession = Depends(get_session),
    subject: str = Depends(current_subject),
):
    """Replace this user's overrides with what the form says.

    The whole set rather than a patch, because a key the form no longer carries
    is a field the user cleared, and clearing is how an override is undone.
    """
    try:
        checked = user_settings.validate_for_write(body.values)
    except ValueError as exc:
        # Refused here, not dropped. A value the page accepted and the server
        # quietly ignored is worse than an error message.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await user_settings.save_values(session, subject, checked)
    return {"values": checked}


@router.put("/secrets/{key}")
async def put_user_secret(
    key: str,
    body: UserSecretIn,
    session: AsyncSession = Depends(get_session),
    subject: str = Depends(current_subject),
):
    """Store one credential, bound to the endpoint it authenticates against."""
    if key not in catalog.SECRET_KEYS:
        raise HTTPException(status_code=400, detail=f"not a credential: {key}")
    try:
        entry = user_secrets.entry(key, body.value, endpoint=body.endpoint)
    except user_secrets.SecretsUnavailable as exc:
        # 409 rather than 400: nothing about the request is wrong, the feature is
        # switched off for this deployment.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await user_settings.save_secret(session, subject, key, entry)
    return {"key": key, "set": True}


@router.delete("/secrets/{key}")
async def delete_user_secret(
    key: str,
    session: AsyncSession = Depends(get_session),
    subject: str = Depends(current_subject),
):
    """Forget one credential. Removes it from the row rather than blanking it."""
    if key not in catalog.SECRET_KEYS:
        raise HTTPException(status_code=400, detail=f"not a credential: {key}")
    await user_settings.delete_secret(session, subject, key)
    return {"key": key, "set": False}


@router.post("/seen")
async def mark_seen(
    body: SeenIn,
    session: AsyncSession = Depends(get_session),
    subject: str = Depends(current_subject),
):
    """Acknowledge the settings introduced since this user last looked."""
    await user_settings.mark_seen(session, subject, body.keys)
    return {"seen": sorted(body.keys)}
