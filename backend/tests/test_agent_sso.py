"""The SSO token registry: refreshing before expiry, and failing loudly after.

`app/agent_sso.py` holds the one credential the backend deliberately keeps —
the refresh token behind a background run — and its whole job is that the token
handed to the agent server always has real life left in it. Three properties
would each fail silently and are pinned here:

  * **The margin is the feature.** Returning a token with 30 seconds left is
    indistinguishable from returning a good one until a question that takes 40
    seconds fails, mid-run, once. The 180s default is asserted directly rather
    than inferred, because a later "tidy-up" that reads the setting from the
    wrong place would still pass a test that only checked refresh-happens-
    eventually.

  * **An expired session must raise, never return `""`.** Empty string is how
    `auth_headers` spells "send no credential", so a silent downgrade would turn
    an expired login into unauthenticated requests against a server doing
    per-user permission control — the most confusing possible outcome, and one
    that looks like an agent bug.

  * **Rotation.** Keycloak may hand back a new refresh token every exchange.
    Keeping the old one works until the realm retires it, which surfaces hours
    into a run and reads as an expired login rather than as this bug.

Plus the switch: with `AGENT_SSO_ENABLED` off — the default — nothing registers
and nothing is held, which is what makes the whole feature inert for every
deployment that has not asked for it.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from app import agent_sso
from app.agent_sso import SsoSessionExpired

KEYCLOAK = "https://kc.test/auth"
TOKEN_URL = f"{KEYCLOAK}/realms/tsmc/protocol/openid-connect/token"


@pytest.fixture(autouse=True)
def _clean_registry():
    """The registry is module state, like `cancellation.py`'s."""
    agent_sso._entries.clear()
    yield
    agent_sso._entries.clear()


def sso_on(configure, **overrides):
    return configure(
        auth_mode="keycloak",
        agent_sso_enabled=True,
        keycloak_url=KEYCLOAK,
        keycloak_realm="tsmc",
        keycloak_client_id="ai4bi-public",
        **overrides,
    )


def token_response(access="at-1", expires_in=600, refresh=None):
    body = {"access_token": access, "expires_in": expires_in, "token_type": "Bearer"}
    if refresh is not None:
        body["refresh_token"] = refresh
    return httpx.Response(200, json=body)


# --- The switch -----------------------------------------------------------


def test_sso_is_off_by_default(configure):
    with configure(auth_mode="keycloak"):
        assert agent_sso.enabled() is False


def test_sso_needs_keycloak_as_well_as_the_flag(configure):
    """Forwarding an identity that is a header anyone can set is the hole
    `services/user_secrets.py` refuses to open for stored credentials."""
    with configure(auth_mode="fake", agent_sso_enabled=True):
        assert agent_sso.enabled() is False
        assert agent_sso.register("run-1", "rt-1", "alice") is False
        assert agent_sso.registered("run-1") is False


def test_registering_is_a_no_op_when_the_browser_sent_nothing(configure):
    with sso_on(configure):
        assert agent_sso.register("run-1", None, "alice") is False
        assert agent_sso.register("run-1", "   ", "alice") is False
        assert agent_sso.registered("run-1") is False


def test_register_and_clear(configure):
    with sso_on(configure):
        assert agent_sso.register("run-1", "rt-1", "alice") is True
        assert agent_sso.registered("run-1") is True
        agent_sso.clear("run-1")
        assert agent_sso.registered("run-1") is False
        agent_sso.clear("run-1")  # a scope that never registered is fine


# --- Minting and the margin -----------------------------------------------


@respx.mock
async def test_the_first_call_mints_a_token(configure):
    route = respx.post(TOKEN_URL).mock(return_value=token_response())
    with sso_on(configure):
        agent_sso.register("run-1", "rt-1", "alice")
        assert await agent_sso.access_token("run-1") == "at-1"
    assert route.call_count == 1
    sent = dict(httpx.QueryParams(route.calls[0].request.content.decode()))
    assert sent["grant_type"] == "refresh_token"
    assert sent["refresh_token"] == "rt-1"
    assert sent["client_id"] == "ai4bi-public"


@respx.mock
async def test_a_token_with_life_left_is_reused(configure):
    route = respx.post(TOKEN_URL).mock(return_value=token_response(expires_in=600))
    with sso_on(configure, agent_sso_refresh_margin_s=180):
        agent_sso.register("run-1", "rt-1", "alice")
        first = await agent_sso.access_token("run-1")
        second = await agent_sso.access_token("run-1")
    assert first == second == "at-1"
    assert route.call_count == 1, (
        "a token with 600s left and a 180s margin must not be re-minted; "
        "refreshing on every call would hammer the identity provider"
    )


@respx.mock
async def test_a_token_inside_the_margin_is_re_minted(configure):
    """The 10-minute token that has 100 seconds left. It would still work for a
    short question and fail for a long one, which is exactly the case the margin
    exists to remove."""
    responses = [token_response("at-1", expires_in=100), token_response("at-2", expires_in=600)]
    route = respx.post(TOKEN_URL).mock(side_effect=responses)
    with sso_on(configure, agent_sso_refresh_margin_s=180):
        agent_sso.register("run-1", "rt-1", "alice")
        assert await agent_sso.access_token("run-1") == "at-1"
        assert await agent_sso.access_token("run-1") == "at-2"
    assert route.call_count == 2


def test_the_default_margin_is_180_seconds():
    """Asserted as a number, not inferred from behaviour. Company access tokens
    live 10 minutes; this is the promise that a refresh happens roughly every 7,
    and a later tidy-up that reads the setting from somewhere else would still
    pass a test that only checked "a refresh happens eventually"."""
    from app.config import Settings

    assert Settings.model_fields["agent_sso_refresh_margin_s"].default == 180


@pytest.mark.parametrize(("margin", "expected_calls"), [(180, 1), (240, 2)])
@respx.mock
async def test_the_margin_setting_is_the_one_actually_applied(
    configure, margin, expected_calls
):
    """200s of life is outside a 180s margin and inside a 240s one, so the two
    cases separate "reused" from "re-minted" on the setting alone."""
    route = respx.post(TOKEN_URL).mock(
        side_effect=[token_response("at-1", expires_in=200)] * 2
    )
    with sso_on(configure, agent_sso_refresh_margin_s=margin):
        agent_sso.register("run-1", "rt-1", "alice")
        await agent_sso.access_token("run-1")
        await agent_sso.access_token("run-1")
    assert route.call_count == expected_calls


@respx.mock
async def test_an_expires_in_of_zero_is_expired_and_not_ten_minutes(configure):
    """`or 600` would map a present `0` to the default and cache a dead token
    for ten minutes — the exact mid-run failure this module exists to prevent."""
    route = respx.post(TOKEN_URL).mock(
        side_effect=[token_response("at-1", expires_in=0), token_response("at-2", expires_in=600)]
    )
    with sso_on(configure):
        agent_sso.register("run-1", "rt-1", "alice")
        assert await agent_sso.access_token("run-1") == "at-1"
        assert await agent_sso.access_token("run-1") == "at-2"
    assert route.call_count == 2


@respx.mock
async def test_a_missing_expires_in_does_not_cause_a_refresh_per_call(configure):
    """A 0-second fallback would re-mint on literally every question."""
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "at-1"})
    )
    with sso_on(configure):
        agent_sso.register("run-1", "rt-1", "alice")
        await agent_sso.access_token("run-1")
        await agent_sso.access_token("run-1")
    assert route.call_count == 1


# --- Rotation -------------------------------------------------------------


@respx.mock
async def test_a_rotated_refresh_token_replaces_the_one_we_hold(configure):
    route = respx.post(TOKEN_URL).mock(
        side_effect=[
            token_response("at-1", expires_in=0, refresh="rt-2"),
            token_response("at-2", expires_in=600, refresh="rt-3"),
        ]
    )
    with sso_on(configure):
        agent_sso.register("run-1", "rt-1", "alice")
        await agent_sso.access_token("run-1")
        await agent_sso.access_token("run-1")

    second = dict(httpx.QueryParams(route.calls[1].request.content.decode()))
    assert second["refresh_token"] == "rt-2", (
        "the second exchange presented the original refresh token; against a "
        "realm that rotates and revokes, that one is already retired"
    )
    assert agent_sso._entries["run-1"].refresh_token == "rt-3"


@respx.mock
async def test_a_response_without_a_refresh_token_keeps_the_existing_one(configure):
    """A realm with rotation off returns no new refresh token, and dropping the
    one we hold would end the session on the first exchange."""
    route = respx.post(TOKEN_URL).mock(
        side_effect=[token_response("at-1", expires_in=0), token_response("at-2", expires_in=600)]
    )
    with sso_on(configure):
        agent_sso.register("run-1", "rt-1", "alice")
        await agent_sso.access_token("run-1")
        await agent_sso.access_token("run-1")
    second = dict(httpx.QueryParams(route.calls[1].request.content.decode()))
    assert second["refresh_token"] == "rt-1"


# --- Failure --------------------------------------------------------------


async def test_an_unregistered_scope_raises_rather_than_sending_nothing(configure):
    with sso_on(configure):
        with pytest.raises(SsoSessionExpired):
            await agent_sso.access_token("never-registered")


@respx.mock
async def test_a_refused_refresh_raises_and_does_not_quote_the_body(configure):
    """A token endpoint's error can echo the grant back, and this text reaches a
    run record and a browser."""
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            400, json={"error": "invalid_grant", "error_description": "Token is not active: rt-1"}
        )
    )
    with sso_on(configure):
        agent_sso.register("run-1", "rt-1", "alice")
        with pytest.raises(SsoSessionExpired) as caught:
            await agent_sso.access_token("run-1")
    assert "rt-1" not in str(caught.value)
    assert "400" in str(caught.value)


@respx.mock
async def test_an_unreachable_identity_provider_says_so(configure):
    """Distinguished from an ended session, so nobody re-logs-in to fix DNS."""
    respx.post(TOKEN_URL).mock(side_effect=httpx.ConnectError("no route"))
    with sso_on(configure):
        agent_sso.register("run-1", "rt-1", "alice")
        with pytest.raises(SsoSessionExpired, match="Could not reach the identity provider"):
            await agent_sso.access_token("run-1")


@respx.mock
async def test_a_response_without_an_access_token_raises(configure):
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"token_type": "Bearer"}))
    with sso_on(configure):
        agent_sso.register("run-1", "rt-1", "alice")
        with pytest.raises(SsoSessionExpired, match="returned no access token"):
            await agent_sso.access_token("run-1")


async def test_a_missing_keycloak_url_is_reported_as_an_expired_session(configure):
    with configure(auth_mode="keycloak", agent_sso_enabled=True, keycloak_url=""):
        agent_sso.register("run-1", "rt-1", "alice")
        with pytest.raises(SsoSessionExpired, match="KEYCLOAK_URL"):
            await agent_sso.access_token("run-1")


# --- Concurrency ----------------------------------------------------------


@respx.mock
async def test_concurrent_callers_trigger_exactly_one_refresh(configure):
    """A run asks up to 32 questions at once. Without the lock, all 32 would
    open their own exchange against Keycloak at the same instant — the stampede
    `keycloak.py` locks its JWKS fetch against."""
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def slow(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return token_response("at-1", expires_in=600)

    respx.post(TOKEN_URL).mock(side_effect=slow)
    with sso_on(configure):
        agent_sso.register("run-1", "rt-1", "alice")
        tasks = [asyncio.create_task(agent_sso.access_token("run-1")) for _ in range(32)]
        await started.wait()
        release.set()
        results = await asyncio.gather(*tasks)

    assert results == ["at-1"] * 32
    assert calls == 1


# --- The credential handed to build_seams ---------------------------------


@respx.mock
async def test_credential_for_resolves_against_the_registry(configure):
    respx.post(TOKEN_URL).mock(return_value=token_response("at-1"))
    with sso_on(configure):
        agent_sso.register("run-1", "rt-1", "alice")
        assert await agent_sso.credential_for("run-1").value() == "at-1"


@respx.mock
async def test_a_credential_follows_a_re_registration(configure):
    """Resume re-registers the same scope with a fresh refresh token. A
    credential that closed over the old entry would keep using the dead one."""
    respx.post(TOKEN_URL).mock(
        side_effect=[token_response("at-1", expires_in=600), token_response("at-2", expires_in=600)]
    )
    with sso_on(configure):
        agent_sso.register("run-1", "rt-1", "alice")
        cred = agent_sso.credential_for("run-1")
        assert await cred.value() == "at-1"

        agent_sso.clear("run-1")
        agent_sso.register("run-1", "rt-9", "alice")
        assert await cred.value() == "at-2"


# --- The marker the browser reads ----------------------------------------


@respx.mock
async def test_every_expiry_message_carries_the_marker(configure):
    """`session_expiry.js` tells "the backend restarted" from "your sign-in
    ended" by looking for `SESSION_MARKER` in the stored `error_message` — the
    only thing on the wire that distinguishes two causes of the same
    `interrupted` status. A message raised without it is silently shown as a
    restart, and the reader is told to press Resume when what they need is to
    sign in.

    Every raise site is exercised rather than grepped: a constant that appears
    in the source but not in the formatted string would pass a grep and fail a
    reader.
    """
    import inspect

    assert inspect.getsource(agent_sso).count("raise SsoSessionExpired(") == 6, (
        "a raise site was added or removed — cover it below, or the browser may "
        "report an ended sign-in as a backend restart"
    )

    messages: list[str] = []

    async def capture(scope: str) -> None:
        with pytest.raises(SsoSessionExpired) as caught:
            await agent_sso.access_token(scope)
        messages.append(str(caught.value))

    # 1. Nothing held for this scope (a restart lost the registry).
    with sso_on(configure):
        await capture("never-registered")

    # 2. A TokenError from keycloak.token_endpoint, wrapped.
    with configure(auth_mode="keycloak", agent_sso_enabled=True, keycloak_url=""):
        agent_sso.register("run-a", "rt-1", "alice")
        await capture("run-a")

    # 3-6. Every way the exchange itself can fail.
    failures = [
        httpx.Response(400, json={"error": "invalid_grant"}),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"token_type": "Bearer"}),
        httpx.ConnectError("no route"),
    ]
    for i, outcome in enumerate(failures):
        agent_sso._entries.clear()
        route = respx.post(TOKEN_URL)
        if isinstance(outcome, Exception):
            route.mock(side_effect=outcome)
        else:
            route.mock(return_value=outcome)
        with sso_on(configure):
            agent_sso.register(f"run-{i}", "rt-1", "alice")
            await capture(f"run-{i}")

    assert len(messages) == 6
    for message in messages:
        assert agent_sso.SESSION_MARKER in message, (
            f"this would be shown to the reader as a backend restart: {message!r}"
        )


def test_the_marker_is_what_the_browser_looks_for():
    """The two halves of the contract, side by side. `session_expiry.js` holds
    its own copy — one string in two languages cannot be shared, so it is
    asserted equal instead."""
    import pathlib as _pathlib
    import re

    js = _pathlib.Path(__file__).parents[2] / "frontend" / "src" / "session_expiry.js"
    found = re.search(r'SESSION_MARKER\s*=\s*"([^"]+)"', js.read_text())
    assert found, "session_expiry.js no longer defines SESSION_MARKER"
    assert found.group(1) == agent_sso.SESSION_MARKER, (
        "the browser is looking for a different phrase than the backend writes, "
        "so an ended sign-in will be reported as a backend restart"
    )
