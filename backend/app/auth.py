"""Login + role guards (§6.16).

Two modes, one seam. `AUTH_MODE=fake` trusts an `X-User-Subject` header, which is
what local development, the seeded demo and the owner/viewer switch in the top
bar all run on. `AUTH_MODE=keycloak` verifies a Keycloak-issued bearer token and
takes the subject from its `preferred_username` claim.

**Only `current_subject` knows the difference.** Roles are not a property of the
user globally — they are resolved per eval_set from `eval_set_roles` using a
subject *string* — so `role_for` / `require_reader` / `require_owner` below are
byte-for-byte what they were before real login existed.

**Why `preferred_username` rather than the token's `sub`**: `sub` is a UUID.
`eval_set_roles.user_subject` and `runs.triggered_by` are text columns that
already hold usernames, the share picker is a person typing a colleague's
username, and the employee directory is keyed by that same username. Storing
`sub` would buy immutability at the cost of a migration, a display-name lookup on
every screen that shows a share list, and a database nobody can read. The trade
is that a username *can* in principle be reassigned; the consequence if that ever
happens is recoverable (an owner re-shares), which is what makes it the right
trade rather than merely the convenient one.

Guard semantics:
    owner  -> full write (edit question/metadata, delete run, re-diagnose) + read + run
    viewer -> read + trigger-run only; no writes, no re-diagnose
"""
from __future__ import annotations

import uuid

from fastapi import Depends, Header, HTTPException, Path, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.keycloak import TokenError, verify_token
from app.models import EvalSetRole


def normalize_subject(raw: str | None) -> str:
    """The single normalisation point for every identity string.

    Both ends of a share have to agree byte-for-byte: `eval_set_roles` is looked
    up with `WHERE user_subject = ?`, so a token carrying `TW12345` and a share
    typed as `tw12345` would be two different people — and the failure is silent,
    an eval set shared with nobody rather than an error. Casefolding here and at
    every write of a subject (see `app/routers/eval_sets.py`) makes that
    divergence impossible rather than unlikely.
    """
    return (raw or "").strip().lower()


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization") or ""
    scheme, _, token = header.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


async def current_subject(
    request: Request,
    x_user_subject: str | None = Header(default=None),
    subject: str | None = Query(default=None),
) -> str:
    """Identity of the caller.

    In keycloak mode the token is only ever read from the `Authorization`
    header. There is deliberately no `?access_token=` fallback: the one caller
    that used to need a query parameter was the SSE stream, and the frontend now
    reads those streams with `fetch` precisely so it can set a header. A token in
    a URL lands in the proxy's access log, which is a poor trade for a fallback
    nothing uses.
    """
    if settings.auth_mode == "fake":
        return normalize_subject(x_user_subject or subject or settings.fake_user_subject)

    token = _bearer(request)
    if not token:
        raise HTTPException(status_code=401, detail="missing bearer token")
    try:
        claims = await verify_token(token)
    except TokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    username = normalize_subject(claims.get("preferred_username"))
    if not username:
        # Every downstream permission check keys off this string, so an empty one
        # would silently grant the identity of "" rather than fail.
        raise HTTPException(status_code=401, detail="token carries no preferred_username")
    return username


async def role_for(session: AsyncSession, eval_set_id: uuid.UUID, subject: str) -> str | None:
    """The caller's role on one eval set, or None. Public because a couple of
    endpoints (run cancel) need a rule the two guards below don't express:
    "owner, or the person who started this run"."""
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
    role = await role_for(session, eval_set_id, subject)
    if role not in ("owner", "viewer"):
        raise HTTPException(status_code=403, detail="no access to this eval set")
    return subject


async def require_owner(
    eval_set_id: uuid.UUID = Path(...),
    subject: str = Depends(current_subject),
    session: AsyncSession = Depends(get_session),
) -> str:
    """Only owner may write / delete / re-diagnose."""
    role = await role_for(session, eval_set_id, subject)
    if role != "owner":
        raise HTTPException(status_code=403, detail="owner role required for this action")
    return subject
