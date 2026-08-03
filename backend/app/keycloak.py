"""Keycloak access-token verification (AUTH_MODE=keycloak).

The platform is a public OIDC client: the browser runs the Authorization Code +
PKCE flow against Keycloak and sends the resulting access token as
`Authorization: Bearer …`. This module answers one question about that token —
*did Keycloak issue it, and is it still valid* — and hands the claims back.
`app/auth.py` takes `preferred_username` from there; nothing else in the backend
knows a token exists.

**Why the key set is fetched here instead of with PyJWT's PyJWKClient**: that
helper fetches over blocking urllib. Called from an async request handler it
stalls the whole event loop, and with 60-second access tokens a key rotation
would stall it repeatedly. `httpx` is already a dependency for the agent and
Langfuse clients, so fetching the key set with it costs nothing extra.

**Three deliberate choices in the failure paths**

1. *An unknown `kid` refetches once.* Keycloak rotates signing keys without
   warning. Trusting the cache blindly would turn a rotation into a window where
   every request 401s until the TTL happens to expire.
2. *A stampede is prevented with a lock.* Every in-flight request misses the
   cache at the same moment, and without the lock each one opens its own
   connection to Keycloak at exactly the time Keycloak is least able to help.
3. *An audience mismatch reports the audience the token actually carries.*
   Keycloak only writes the client id into `aud` when an audience mapper says
   so; without one it writes something else, `account` most often. That single
   unknown makes every token fail, and the message is what turns a day of
   guessing into changing one environment variable (§4.11: an error has to say
   which kind of error it is).
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import jwt

from app.config import settings


class TokenError(Exception):
    """The bearer token is missing, malformed, expired, or not ours.

    One type for every rejection: the caller turns it into a 401 and the message
    is what distinguishes the cases.
    """


# Signing keys, cached across requests. `_lock` guards the refetch, not reads —
# a stale read is harmless, a stampede is not.
_jwks: dict[str, Any] | None = None
_jwks_at: float = 0.0
_lock = asyncio.Lock()


def _realm_base() -> str:
    """`{url}/realms/{realm}`, the prefix every OIDC endpoint hangs off.

    `keycloak_url` is copied verbatim from the deployment (it may or may not end
    in the historical `/auth`), so only the trailing slash is normalised.
    """
    if not settings.keycloak_url:
        raise TokenError("KEYCLOAK_URL is not set, but AUTH_MODE=keycloak")
    return f"{settings.keycloak_url.rstrip('/')}/realms/{settings.keycloak_realm}"


def issuer() -> str:
    """The `iss` value Keycloak stamps on tokens from this realm."""
    return _realm_base()


async def _fetch_jwks() -> dict[str, Any]:
    url = f"{_realm_base()}/protocol/openid-connect/certs"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            body = response.json()
    except httpx.HTTPStatusError as exc:  # reachable, but not serving keys
        raise TokenError(
            f"could not read signing keys from {url}: HTTP {exc.response.status_code} "
            f"{exc.response.text[:200]}"
        ) from exc
    except httpx.HTTPError as exc:  # DNS, TLS, connect, timeout
        raise TokenError(f"could not reach {url}: {exc}") from exc
    except ValueError as exc:
        raise TokenError(f"{url} did not return JSON") from exc

    if not isinstance(body, dict) or not isinstance(body.get("keys"), list):
        raise TokenError(f"{url} returned no 'keys' array")
    return body


async def _jwk_for(kid: str | None) -> dict[str, Any]:
    """The JWK matching this token's `kid`, refetching once if it is unknown."""
    global _jwks, _jwks_at

    def found() -> dict[str, Any] | None:
        if _jwks is None:
            return None
        for key in _jwks["keys"]:
            # A key set with a single unlabelled key is legal; match it rather
            # than refetching forever against a Keycloak that omits `kid`.
            if kid is None or key.get("kid") == kid:
                return key
        return None

    fresh = _jwks is not None and (time.monotonic() - _jwks_at) < settings.keycloak_jwks_cache_s
    hit = found()
    if hit is not None and fresh:
        return hit

    async with _lock:
        # Another request may have refetched while this one waited.
        hit = found()
        fresh = _jwks is not None and (time.monotonic() - _jwks_at) < settings.keycloak_jwks_cache_s
        if hit is not None and fresh:
            return hit
        _jwks = await _fetch_jwks()
        _jwks_at = time.monotonic()

    hit = found()
    if hit is None:
        raise TokenError(f"no signing key with kid={kid!r} in the realm's key set")
    return hit


def _reset_cache() -> None:
    """Drop the cached key set. Used by tests; harmless in production."""
    global _jwks, _jwks_at
    _jwks = None
    _jwks_at = 0.0


def _audience_of(token: str) -> Any:
    """The `aud` the token actually carries, for the mismatch message.

    Reads the payload without verifying anything — by the time this is called the
    signature has already been checked, and the value only ever reaches an error
    string.
    """
    try:
        return jwt.decode(token, options={"verify_signature": False}).get("aud")
    except Exception:  # noqa: BLE001 - diagnostics must never mask the real error
        return None


async def verify_token(token: str) -> dict[str, Any]:
    """Verify signature, issuer, expiry and (optionally) audience.

    Returns the claims. Raises `TokenError` with a message that names what was
    wrong — the caller has no way to find out otherwise, and neither does the
    person reading the log.
    """
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise TokenError(f"malformed bearer token: {exc}") from exc

    jwk = await _jwk_for(header.get("kid"))
    try:
        key = jwt.PyJWK.from_dict(jwk, algorithm=jwk.get("alg") or "RS256").key
    except jwt.PyJWTError as exc:
        raise TokenError(f"unusable signing key in the realm's key set: {exc}") from exc

    audience = settings.keycloak_audience.strip()
    try:
        return jwt.decode(
            token,
            key=key,
            algorithms=[jwk.get("alg") or "RS256"],
            issuer=issuer(),
            audience=audience or None,
            # Access tokens here live 60 seconds, so a machine whose clock is a
            # few seconds fast would reject tokens that are genuinely current.
            leeway=10,
            options={"verify_aud": bool(audience), "require": ["exp", "iss"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("token has expired") from exc
    except jwt.InvalidAudienceError as exc:
        raise TokenError(
            f"expected audience {audience!r}, token has {_audience_of(token)!r} — "
            "set KEYCLOAK_AUDIENCE to that value, or leave it blank to skip the check"
        ) from exc
    except jwt.InvalidIssuerError as exc:
        raise TokenError(f"token was not issued by {issuer()}") from exc
    except jwt.PyJWTError as exc:
        raise TokenError(f"token rejected: {exc}") from exc
