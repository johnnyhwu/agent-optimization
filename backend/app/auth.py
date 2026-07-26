"""Fake login + role guards (§6.16).

Stage 1 fakes the logged-in user via config (FAKE_USER_SUBJECT). Roles are NOT a
property of the user globally — they are resolved per eval_set from
`eval_set_roles`. A real deployment would replace `current_subject` with token
(key-lock) verification; the role-lookup dependencies below stay unchanged.

Guard semantics:
    owner  -> full write (edit question/metadata, delete run, re-diagnose) + read + run
    viewer -> read + trigger-run only; no writes, no re-diagnose
"""
from __future__ import annotations

import uuid

from fastapi import Depends, Header, HTTPException, Path, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import EvalSetRole


def current_subject(
    x_user_subject: str | None = Header(default=None),
    subject: str | None = Query(default=None),
) -> str:
    """Identity of the caller.

    Fake login: an optional `X-User-Subject` header (or `?subject=` query param,
    used by the SSE stream since EventSource can't set headers) overrides the
    config default, so the UI/tests can flip owner<->viewer without restarting.
    """
    return (x_user_subject or subject or settings.fake_user_subject).strip()


async def _role_for(session: AsyncSession, eval_set_id: uuid.UUID, subject: str) -> str | None:
    row = await session.scalar(
        select(EvalSetRole.role).where(
            EvalSetRole.eval_set_id == eval_set_id,
            EvalSetRole.user_subject == subject,
        )
    )
    return row


async def require_reader(
    eval_set_id: uuid.UUID = Path(...),
    subject: str = Depends(current_subject),
    session: AsyncSession = Depends(get_session),
) -> str:
    """owner or viewer may read (and trigger runs)."""
    role = await _role_for(session, eval_set_id, subject)
    if role not in ("owner", "viewer"):
        raise HTTPException(status_code=403, detail="no access to this eval set")
    return subject


async def require_owner(
    eval_set_id: uuid.UUID = Path(...),
    subject: str = Depends(current_subject),
    session: AsyncSession = Depends(get_session),
) -> str:
    """Only owner may write / delete / re-diagnose."""
    role = await _role_for(session, eval_set_id, subject)
    if role != "owner":
        raise HTTPException(status_code=403, detail="owner role required for this action")
    return subject
