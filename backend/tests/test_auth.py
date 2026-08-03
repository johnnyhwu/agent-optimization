"""Identity: the fake header and the Keycloak bearer token (§6.16).

Two things carry most of the weight here.

The first is that **fake mode is untouched**. Everything else in this suite calls
router functions with `subject="alice"` directly, so if `current_subject` had
quietly changed shape nothing else would have caught it.

The second is the audience message. Keycloak only writes the client id into `aud`
when an audience mapper says so; without one it writes something else. That one
unknown makes *every* token fail, so the test asserts the rejection names the
value the token actually carried — that string is the difference between changing
an environment variable and a day of guessing.
"""
from __future__ import annotations

import datetime as dt
import json

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from jwt.algorithms import RSAAlgorithm
from starlette.requests import Request

from app import keycloak
from app.auth import current_subject, normalize_subject

KEYCLOAK_URL = "https://keycloak.test/auth"
REALM = "tsmc"
ISSUER = f"{KEYCLOAK_URL}/realms/{REALM}"
CERTS_URL = f"{ISSUER}/protocol/openid-connect/certs"
KID = "test-key-1"

_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwks(kid: str = KID) -> dict:
    """The realm's public key set, in the shape Keycloak serves it."""
    entry = json.loads(RSAAlgorithm.to_jwk(_key.public_key()))
    entry.update({"kid": kid, "alg": "RS256", "use": "sig"})
    return {"keys": [entry]}


def token(kid: str = KID, **claims) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "iss": ISSUER,
        "aud": "ai4bi-public",
        "sub": "f81d4fae-7dec-11d0-a765-00a0c91e6bf6",
        "preferred_username": "TW12345",
        "exp": now + dt.timedelta(minutes=1),
        "iat": now,
    }
    payload.update(claims)
    return jwt.encode(payload, _key, algorithm="RS256", headers={"kid": kid})


def request_with(**headers) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request(
        {"type": "http", "method": "GET", "path": "/", "headers": raw, "query_string": b""}
    )


@pytest.fixture(autouse=True)
def clean_jwks_cache():
    keycloak._reset_cache()
    yield
    keycloak._reset_cache()


@pytest.fixture
def keycloak_mode(configure):
    with configure(
        auth_mode="keycloak",
        keycloak_url=KEYCLOAK_URL,
        keycloak_realm=REALM,
        keycloak_audience="ai4bi-public",
    ) as settings:
        yield settings


# --- normalisation ---------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [("TW12345", "tw12345"), ("  tw12345  ", "tw12345"), ("Tw12345", "tw12345"),
     (None, ""), ("", "")],
)
def test_subject_normalisation_is_case_and_whitespace_insensitive(raw, expected):
    """A share typed as TW12345 and a token carrying tw12345 must be one person.

    `eval_set_roles` is looked up by exact string, so divergence here is not an
    error — it is an eval set shared with an account that never signs in.
    """
    assert normalize_subject(raw) == expected


# --- fake mode is unchanged ------------------------------------------------


async def test_fake_mode_still_trusts_the_header(configure):
    with configure(auth_mode="fake"):
        assert await current_subject(request_with(), x_user_subject="bob", subject=None) == "bob"


async def test_fake_mode_still_accepts_the_query_param_and_falls_back_to_config(configure):
    with configure(auth_mode="fake", fake_user_subject="alice"):
        assert await current_subject(request_with(), x_user_subject=None, subject="carol") == "carol"
        assert await current_subject(request_with(), x_user_subject=None, subject=None) == "alice"


async def test_fake_mode_ignores_a_bearer_token(configure):
    """Otherwise a developer running the real frontend against a fake backend
    would get an identity nobody configured."""
    with configure(auth_mode="fake", fake_user_subject="alice"):
        req = request_with(authorization=f"Bearer {token()}")
        assert await current_subject(req, x_user_subject=None, subject=None) == "alice"


# --- keycloak mode: the happy path -----------------------------------------


@respx.mock
async def test_valid_token_yields_the_normalised_preferred_username(keycloak_mode):
    """The claim is `preferred_username`, not `sub`.

    Guarding a real hazard: the reference implementation this was written from
    misspelled the claim and silently fell through to `sub`, which would have put
    UUIDs into eval_set_roles where usernames belong.
    """
    respx.get(CERTS_URL).mock(return_value=httpx.Response(200, json=jwks()))
    req = request_with(authorization=f"Bearer {token()}")

    assert await current_subject(req, x_user_subject=None, subject=None) == "tw12345"


@respx.mock
async def test_the_header_is_ignored_in_keycloak_mode(keycloak_mode):
    """The fake header must not be a way around the token."""
    respx.get(CERTS_URL).mock(return_value=httpx.Response(200, json=jwks()))
    req = request_with(authorization=f"Bearer {token()}")

    assert await current_subject(req, x_user_subject="alice", subject="alice") == "tw12345"


@respx.mock
async def test_the_key_set_is_cached_across_requests(keycloak_mode):
    route = respx.get(CERTS_URL).mock(return_value=httpx.Response(200, json=jwks()))
    for _ in range(3):
        await current_subject(
            request_with(authorization=f"Bearer {token()}"), x_user_subject=None, subject=None
        )
    assert route.call_count == 1


@respx.mock
async def test_an_unknown_kid_refetches_the_key_set_once(keycloak_mode):
    """Keycloak rotates signing keys without warning. Trusting the cache blindly
    turns a rotation into a window where every request 401s until the TTL
    happens to lapse."""
    route = respx.get(CERTS_URL).mock(return_value=httpx.Response(200, json=jwks(kid="old")))
    await current_subject(
        request_with(authorization=f"Bearer {token(kid='old')}"), x_user_subject=None, subject=None
    )
    assert route.call_count == 1

    # The realm rotates: same endpoint, a key set the cache has never seen.
    route.mock(return_value=httpx.Response(200, json=jwks(kid="new")))
    subject = await current_subject(
        request_with(authorization=f"Bearer {token(kid='new')}"), x_user_subject=None, subject=None
    )

    assert subject == "tw12345"
    # Exactly one refetch — the unknown kid must not turn into a retry loop that
    # hammers Keycloak on every request.
    assert route.call_count == 2


# --- keycloak mode: every rejection -----------------------------------------


@respx.mock
async def test_a_missing_token_is_a_401(keycloak_mode):
    with pytest.raises(HTTPException) as exc:
        await current_subject(request_with(), x_user_subject=None, subject=None)
    assert exc.value.status_code == 401
    assert "missing bearer" in exc.value.detail


@respx.mock
@pytest.mark.parametrize("header", ["Basic abc", "Bearer", "Bearer   ", "token abc"])
async def test_a_non_bearer_authorization_header_is_a_401(keycloak_mode, header):
    with pytest.raises(HTTPException) as exc:
        await current_subject(
            request_with(authorization=header), x_user_subject=None, subject=None
        )
    assert exc.value.status_code == 401


@respx.mock
async def test_a_token_signed_by_someone_else_is_a_401(keycloak_mode):
    respx.get(CERTS_URL).mock(return_value=httpx.Response(200, json=jwks()))
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode(
        {"iss": ISSUER, "aud": "ai4bi-public", "preferred_username": "mallory",
         "exp": dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=1)},
        other,
        algorithm="RS256",
        headers={"kid": KID},
    )

    with pytest.raises(HTTPException) as exc:
        await current_subject(
            request_with(authorization=f"Bearer {forged}"), x_user_subject=None, subject=None
        )
    assert exc.value.status_code == 401


@respx.mock
async def test_an_expired_token_is_a_401(keycloak_mode):
    respx.get(CERTS_URL).mock(return_value=httpx.Response(200, json=jwks()))
    stale = token(exp=dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5))

    with pytest.raises(HTTPException) as exc:
        await current_subject(
            request_with(authorization=f"Bearer {stale}"), x_user_subject=None, subject=None
        )
    assert exc.value.status_code == 401
    assert "expired" in exc.value.detail


@respx.mock
async def test_a_token_from_another_realm_is_a_401(keycloak_mode):
    respx.get(CERTS_URL).mock(return_value=httpx.Response(200, json=jwks()))
    foreign = token(iss="https://keycloak.test/auth/realms/somewhere-else")

    with pytest.raises(HTTPException) as exc:
        await current_subject(
            request_with(authorization=f"Bearer {foreign}"), x_user_subject=None, subject=None
        )
    assert exc.value.status_code == 401


@respx.mock
async def test_a_token_without_preferred_username_is_a_401(keycloak_mode):
    """An empty subject would authorise as the identity "" rather than fail."""
    respx.get(CERTS_URL).mock(return_value=httpx.Response(200, json=jwks()))
    anonymous = token()
    anonymous = jwt.encode(
        {k: v for k, v in jwt.decode(anonymous, options={"verify_signature": False}).items()
         if k != "preferred_username"},
        _key,
        algorithm="RS256",
        headers={"kid": KID},
    )

    with pytest.raises(HTTPException) as exc:
        await current_subject(
            request_with(authorization=f"Bearer {anonymous}"), x_user_subject=None, subject=None
        )
    assert exc.value.status_code == 401
    assert "preferred_username" in exc.value.detail


@respx.mock
async def test_an_unreachable_keycloak_is_a_401_naming_the_url(keycloak_mode):
    """Not a 500: the request genuinely cannot be authenticated. But the reason
    has to survive into the message, or a misconfigured KEYCLOAK_URL looks
    exactly like a bad token."""
    respx.get(CERTS_URL).mock(side_effect=httpx.ConnectError("nope"))

    with pytest.raises(HTTPException) as exc:
        await current_subject(
            request_with(authorization=f"Bearer {token()}"), x_user_subject=None, subject=None
        )
    assert exc.value.status_code == 401
    assert CERTS_URL in exc.value.detail


# --- the audience knob ------------------------------------------------------


@respx.mock
async def test_an_audience_mismatch_reports_the_audience_the_token_carries(keycloak_mode):
    """The single most valuable error message in this module.

    `aud` is a deployment unknown — Keycloak's default is often "account", not
    the client id — and getting it wrong fails every token at once. Naming the
    actual value turns that from an investigation into an env-var change.
    """
    respx.get(CERTS_URL).mock(return_value=httpx.Response(200, json=jwks()))
    mismatched = token(aud="account")

    with pytest.raises(HTTPException) as exc:
        await current_subject(
            request_with(authorization=f"Bearer {mismatched}"), x_user_subject=None, subject=None
        )
    assert exc.value.status_code == 401
    assert "'ai4bi-public'" in exc.value.detail  # what we expected
    assert "'account'" in exc.value.detail       # what actually arrived
    assert "KEYCLOAK_AUDIENCE" in exc.value.detail


@respx.mock
async def test_a_blank_audience_setting_skips_the_check(configure):
    """The escape hatch: a deployment whose `aud` we cannot influence still works."""
    respx.get(CERTS_URL).mock(return_value=httpx.Response(200, json=jwks()))
    with configure(
        auth_mode="keycloak",
        keycloak_url=KEYCLOAK_URL,
        keycloak_realm=REALM,
        keycloak_audience="",
    ):
        req = request_with(authorization=f"Bearer {token(aud='something-else')}")
        assert await current_subject(req, x_user_subject=None, subject=None) == "tw12345"
