"""The signed-in user's SSO token, kept alive for as long as their run needs it.

The platform already verifies a Keycloak access token on every request, and
`app/keycloak.py` says plainly that it then forgets it: "nothing else in the
backend knows a token exists". That is the right default. This module is the one
deliberate exception, for deployments whose agent server does its own per-user
permission control and therefore needs to be told *who* is asking.

**The problem is lifetime, not format.** `Authorization: Bearer <token>` is
already what `integrations/real/agent_auth.auth_headers` emits. But a run is an
`asyncio.create_task` background job that outlives the request that started it —
minutes for an eval, hours for an optimization — and an access token lives ten.
A token captured at trigger time is dead by the second question.

**Nothing here is persisted.** The refresh token arrives on the request that
starts the work, lives in this process, and goes away when the run does. Three
things follow, and they are the reason this shape was chosen over a table:

  * There is no migration, no encryption key to rotate, no row to sweep up, and
    no long-lived user credential sitting in a database.
  * `runs.secrets` and its two siblings never see it, so "credentials never
    leave the server" keeps the structural guarantee `services/user_secrets.py`
    describes rather than gaining an exception.
  * A backend restart loses the tokens — which costs nothing, because a restart
    already forces an eval run to `failed` and an optimization run to
    `interrupted` (`app/main.py`, `app/optimizer/runner.py`), and Resume is a
    deliberate creator-only button. The browser is therefore present to hand
    over a fresh token at exactly the moment one is needed.

Same in-process registry shape as `app/cancellation.py` and `app/sse.py`, and
the same constraint behind it: one backend process owns the background tasks
(§5.3, §15.2). If that is ever lifted this has to move with it — a second worker
would hold no token for a run the first one started.

**Refresh-token rotation.** Keycloak may return a new refresh token on every
exchange. Whatever comes back replaces what we hold, because the alternative is
authenticating happily for seven minutes and then presenting a refresh token the
server retired. Note that the browser refreshes the *same* token for its own
requests (`frontend/src/auth.js`, at a 30s margin); if the realm also revokes on
rotation, the two would invalidate each other, and that is a realm setting to
confirm rather than something this module can paper over.
"""
from __future__ import annotations

import asyncio
import logging
import time

import httpx

from app import keycloak
from app.config import settings
from app.integrations.real.agent_auth import Credential, StaticCredential

log = logging.getLogger(__name__)


# The phrase every `SsoSessionExpired` message carries.
#
# It exists because the browser has to tell two causes of `interrupted` apart —
# a backend restart, which is resumable now, and an ended sign-in, which is
# resumable only after signing in again — and the stored `error_message` is the
# only thing on the wire that distinguishes them. Matching on incidental
# wording is how that breaks silently the first time a message is reworded, so
# the wording carries one deliberate constant instead. `session_expiry.js`
# matches this, and `tests/test_agent_sso.py` checks every raise site has it.
#
# Phrased to read as English in the middle of a sentence, because these
# messages are shown to people, not parsed by them.
SESSION_MARKER = "sign-in session"


class SsoSessionExpired(RuntimeError):
    """The user's SSO session can no longer produce a token for the agent server.

    A distinct type because it is the one agent-side failure that is not the
    agent's fault and not this question's fault: it ends the whole run, and the
    two run flows end it differently — an optimization run becomes `interrupted`
    and keeps every finished step, an eval run becomes `failed`. Anything that
    catches this per question would turn one expired login into a screenful of
    identical agent errors.
    """


class _Entry:
    """One scope's tokens: the refresh token, and the access token last minted."""

    __slots__ = ("refresh_token", "access_token", "expires_at", "subject", "lock")

    def __init__(self, refresh_token: str, subject: str) -> None:
        self.refresh_token = refresh_token
        self.subject = subject
        self.access_token: str = ""
        self.expires_at: float = 0.0
        # Guards the exchange, not the read. An optimization run asks up to 32
        # questions at once, and without this every one of them would open its
        # own refresh against Keycloak at the same instant — the same stampede
        # `keycloak.py` locks the JWKS fetch against.
        self.lock = asyncio.Lock()


_entries: dict[str, _Entry] = {}


def enabled() -> bool:
    """Whether this deployment forwards the caller's identity to the agent server.

    Both halves are required. `AGENT_SSO_ENABLED` alone under `AUTH_MODE=fake`
    would mean forwarding an identity that is a header anyone can set, which is
    the same hole `services/user_secrets.py` refuses to open for stored
    credentials.
    """
    return bool(settings.agent_sso_enabled) and settings.auth_mode == "keycloak"


def register(scope_id, refresh_token: str | None, subject: str = "") -> bool:
    """Hold a refresh token for one unit of background work.

    `scope_id` is whatever identifies that work — a run id, an optimization run
    id, a playground attempt id. Returns whether anything was stored, so a
    caller can tell "SSO is off or the browser sent nothing" from "ready", and
    say so before starting work that would fail on its first question.
    """
    if not enabled():
        return False
    token = (refresh_token or "").strip()
    if not token:
        return False
    _entries[str(scope_id)] = _Entry(token, subject)
    return True


def refusal_reason(refresh_token: str | None, fallback_key: str | None = "") -> str | None:
    """Why long-running work must not start, or `None` when it may.

    Registering is best-effort by design — `register` just says whether it
    stored anything — but *starting a run anyway* is not. Under SSO forwarding
    an agent server that wants a token gets none, and the result is a run that
    reaches every question and fails all of them, with the real cause (a browser
    that sent no session) appearing nowhere. A pre-flight refusal costs one
    request; the alternative costs a whole run and reads as an agent fault.

    `fallback_key` is the escape hatch: a deployment can forward identities
    *and* have one agent behind a gateway that wants its own key. Where there is
    a key to send, there is a credential and nothing to refuse — the same
    precedence `resolve_credential` applies.
    """
    if not enabled():
        return None
    if (refresh_token or "").strip() or (fallback_key or "").strip():
        return None
    return (
        "This deployment sends your sign-in to the agent server, but your "
        "browser supplied no session. Sign in again and retry — or enter an API "
        "key for this agent under Endpoint authentication."
    )


def clear(scope_id) -> None:
    """Drop a scope's tokens. Safe for a scope that never registered."""
    _entries.pop(str(scope_id), None)


def registered(scope_id) -> bool:
    return str(scope_id) in _entries


class _ScopeCredential:
    """The `Credential` a run's agent client resolves against this registry."""

    def __init__(self, scope_id: str) -> None:
        self._scope_id = scope_id

    async def value(self) -> str:
        return await access_token(self._scope_id)


def seam_kwargs(scope_id) -> dict:
    """The `build_seams` keyword for this scope, or nothing at all.

    A dict of kwargs rather than a value, and empty rather than
    `agent_credential=None`, for the reason `pipeline.call_agent` gives for the
    same shape: a caller with no SSO session makes the call it has always made,
    down to the argument list. That keeps the switched-off path obviously inert
    and keeps every `build_seams` stub that predates this working unchanged.
    """
    if not registered(scope_id):
        return {}
    return {"agent_credential": credential_for(scope_id)}


def probe_key(api_key: str | None, caller_token: str | None) -> str:
    """The credential for an agent call made *inside* a request.

    The synchronous counterpart to the registry, and one rule rather than five
    copies of it. A probe, a skills read or a wizard check finishes while the
    caller is still on the other end of the socket, so it has their own bearer
    token to hand and needs no refresh: the browser renewed it at a 30s margin
    before sending. Nothing is registered and nothing can expire — the registry
    exists only for work that outlives the request.

    Same precedence as `agent_auth.resolve_credential`, which is the rule for
    the background half: a key typed for this agent wins, then the signed-in
    user, then — by returning `""` and letting the seam fall back — the
    deployment's own `AGENT_API_KEY`.

    Every endpoint that reads the agent server needs this. Under
    `AGENT_SSO_ENABLED` with no deployment-wide key there is no other credential
    in play, so a site that forgets it does not degrade gracefully: a healthy
    agent answers 401, which reads on the screen as a broken agent server.

    **What this hands out, and to whom.** The destination is a URL the caller
    typed — that is deliberate throughout this platform (a probe that always
    asked the deployment's default could go green against one agent while the
    run went to another), and it is unchanged here. What *is* new is the
    credential: it used to be a service key somebody chose to enter, and under
    SSO it is the caller's own bearer token. So a developer who types an
    unrelated host into the conformance page or the playground sends their own
    identity to it. The token is audience-bound to the agent server, which
    limits what a third party could do with it, and the whole path is off unless
    an operator turns `AGENT_SSO_ENABLED` on — but restricting the destination
    to the deployment's own agent would break pointing at a dev agent, which is
    the case these screens exist for. Whether that trade is right for a given
    realm is the operator's call, and this is where it is written down.
    """
    typed = (api_key or "").strip()
    if typed:
        return typed
    if caller_token and enabled():
        return caller_token
    return ""


def probe_kwargs(caller_token: str | None) -> dict:
    """`probe_key`'s rule as a `build_seams` keyword, for the callers that pass
    a whole secrets dict rather than a single key — writing the token into that
    dict would put it one `model_dump()` away from a `secrets` column.

    Empty rather than `agent_credential=None`, for the reason `seam_kwargs`
    gives: a deployment without SSO makes the call it has always made, down to
    the argument list.
    """
    key = probe_key(None, caller_token)
    return {"agent_credential": StaticCredential(key)} if key else {}


def credential_for(scope_id) -> Credential:
    """The credential to hand `build_seams(agent_credential=...)` for this scope.

    Bound to the id rather than to the entry, so a resumed run picks up the
    token registered by the Resume request instead of a closed-over stale one.
    """
    return _ScopeCredential(str(scope_id))


async def access_token(scope_id) -> str:
    """A token with real life left in it, refreshing first if necessary.

    Raises `SsoSessionExpired` rather than returning `""`. An empty string is
    how `auth_headers` spells "this deployment sends no credential", and a run
    whose login has expired must not quietly downgrade into an unauthenticated
    one — against an agent server doing per-user permission control that would
    be the single most confusing possible outcome.
    """
    key = str(scope_id)
    entry = _entries.get(key)
    if entry is None:
        raise SsoSessionExpired(
            f"This run holds no {SESSION_MARKER} — it was most likely started "
            "before the backend restarted. Start it again from the browser."
        )

    margin = max(int(settings.agent_sso_refresh_margin_s or 0), 0)
    if entry.access_token and entry.expires_at - time.monotonic() > margin:
        return entry.access_token

    async with entry.lock:
        # Re-checked under the lock: while we waited, whoever held it has
        # already minted the token this call was about to ask for.
        if entry.access_token and entry.expires_at - time.monotonic() > margin:
            return entry.access_token
        await _refresh(entry)
        return entry.access_token


async def _refresh(entry: _Entry) -> None:
    """Exchange the refresh token, storing both halves of what comes back."""
    try:
        url = keycloak.token_endpoint()
    except keycloak.TokenError as exc:
        raise SsoSessionExpired(
            f"This run's {SESSION_MARKER} cannot be renewed: {exc}"
        ) from exc

    data = {
        "grant_type": "refresh_token",
        "client_id": settings.keycloak_client_id,
        "refresh_token": entry.refresh_token,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, data=data)
    except httpx.HTTPError as exc:
        # Keycloak unreachable is not the same as a session that ended, but for
        # the run it is: there is no token to make the next call with. The
        # message says which so nobody re-logs-in to fix a DNS problem.
        raise SsoSessionExpired(
            f"Could not reach the identity provider to renew this "
            f"{SESSION_MARKER}: {exc}"
        ) from exc

    if resp.status_code >= 400:
        # Deliberately not quoting the body: a token endpoint's error can echo
        # the grant back, and this text reaches a run record and a browser.
        raise SsoSessionExpired(
            f"The identity provider refused to renew this {SESSION_MARKER} "
            f"(HTTP {resp.status_code}). Signing in again will start a new one."
        )
    try:
        body = resp.json()
    except ValueError as exc:
        raise SsoSessionExpired(
            f"The identity provider did not return JSON when renewing this "
            f"{SESSION_MARKER}."
        ) from exc

    token = body.get("access_token") if isinstance(body, dict) else None
    if not isinstance(token, str) or not token:
        raise SsoSessionExpired(
            f"The identity provider returned no access token for this "
            f"{SESSION_MARKER}."
        )

    # `expires_in` is seconds and Keycloak always sends it. The fallback for an
    # *absent* one is the documented 10-minute default rather than 0, which
    # would re-refresh on every single call. But a present `0` means expired and
    # has to stay 0 — mapping it to 600 would cache a dead token for ten
    # minutes, which is precisely the mid-run failure this module exists to
    # prevent. Hence an explicit None check rather than `or`.
    raw_lifetime = body.get("expires_in")
    if raw_lifetime is None:
        lifetime = 600.0
    else:
        try:
            lifetime = float(raw_lifetime)
        except (TypeError, ValueError):
            lifetime = 600.0

    entry.access_token = token
    entry.expires_at = time.monotonic() + max(lifetime, 0.0)

    # Rotation: keep whatever we were just given. Holding the old one would work
    # until the realm retired it, which is a failure that would surface hours
    # into a run and look like an expired login rather than a bug here.
    rotated = body.get("refresh_token") if isinstance(body, dict) else None
    if isinstance(rotated, str) and rotated.strip():
        entry.refresh_token = rotated.strip()
