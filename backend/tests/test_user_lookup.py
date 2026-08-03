"""The share picker's directory lookup (§6.16).

The point of this endpoint is a failure that produces no error: `INSERT INTO
eval_set_roles` succeeds for a username nobody owns, so a typo shares an eval set
with an account that will never sign in, and the person who typed it is never
told.

The tests below are mostly about the *second* distinction — between "the
directory says no" and "the directory did not answer". Collapsing those two into
one answer is the tempting simplification, and it is wrong in both directions:
block on both and a directory outage stops all sharing everywhere; allow on both
and the typo is back.
"""
from __future__ import annotations

import httpx
import pytest
import respx
from fastapi import HTTPException

from app.routers.users import lookup_user, users

HR_BASE = "https://directory.test/employees"


@pytest.fixture
def keycloak_mode(configure):
    with configure(auth_mode="keycloak", hr_api_base_url=HR_BASE, hr_api_timeout_s=1.0):
        yield


# --- fake mode: no directory to call ---------------------------------------


async def test_fake_mode_resolves_against_the_seeded_user_list(configure):
    """Local development and the seeded demo have no directory. Requiring one
    would make the share dialog unusable offline."""
    with configure(auth_mode="fake", known_users=["alice", "bob"]):
        assert (await lookup_user(username="alice", subject="alice"))["verified"] is True

        with pytest.raises(HTTPException) as exc:
            await lookup_user(username="nobody", subject="alice")
        assert exc.value.status_code == 404


async def test_fake_mode_still_lists_the_switchable_identities(configure):
    with configure(auth_mode="fake", known_users=["alice", "bob"]):
        assert await users(subject="alice") == {"users": ["alice", "bob"], "current": "alice"}


async def test_keycloak_mode_offers_no_identities_to_switch_to(configure):
    """Identity comes from the token, so the top-bar switcher has to disappear —
    and the way the frontend knows to hide it is this empty list."""
    with configure(auth_mode="keycloak"):
        assert await users(subject="tw12345") == {"users": [], "current": "tw12345"}


# --- keycloak mode: the directory answers -----------------------------------


@respx.mock
async def test_a_known_username_returns_the_employee_name(keycloak_mode):
    respx.get(f"{HR_BASE}/tw12345").mock(
        return_value=httpx.Response(200, json={"employee_id": "12345", "employee_name": "Wang"})
    )

    assert await lookup_user(username="TW12345", subject="me") == {
        "username": "tw12345",
        "employee_name": "Wang",
        "verified": True,
    }


@respx.mock
async def test_the_username_is_normalised_before_the_directory_is_asked(keycloak_mode):
    """The lookup and the row that gets written have to agree; normalising in one
    place and not the other is how a verified name still ends up unshareable."""
    route = respx.get(f"{HR_BASE}/tw12345").mock(
        return_value=httpx.Response(200, json={"employee_name": "Wang"})
    )

    result = await lookup_user(username="  TW12345 ", subject="me")

    assert route.called
    assert result["username"] == "tw12345"


@respx.mock
async def test_a_404_from_the_directory_blocks(keycloak_mode):
    respx.get(f"{HR_BASE}/ghost").mock(
        return_value=httpx.Response(404, json={"detail": "not found"})
    )

    with pytest.raises(HTTPException) as exc:
        await lookup_user(username="ghost", subject="me")
    assert exc.value.status_code == 404


@respx.mock
async def test_a_200_carrying_only_detail_also_blocks(keycloak_mode):
    """Some deployments serve a miss as 200 with `{"detail": …}`. Trusting the
    status code alone would wave those straight through as verified."""
    respx.get(f"{HR_BASE}/ghost").mock(
        return_value=httpx.Response(200, json={"detail": "not found"})
    )

    with pytest.raises(HTTPException) as exc:
        await lookup_user(username="ghost", subject="me")
    assert exc.value.status_code == 404


async def test_an_empty_username_blocks_without_calling_the_directory(keycloak_mode):
    with pytest.raises(HTTPException) as exc:
        await lookup_user(username="   ", subject="me")
    assert exc.value.status_code == 404


# --- keycloak mode: the directory does not answer ---------------------------


@respx.mock
async def test_a_timeout_is_unverified_rather_than_rejected(keycloak_mode):
    """An outage over there must not stop everyone here from sharing anything.
    The caller is told the name is unconfirmed, not that it is wrong."""
    respx.get(f"{HR_BASE}/tw12345").mock(side_effect=httpx.ReadTimeout("slow"))

    result = await lookup_user(username="tw12345", subject="me")

    assert result["verified"] is False
    assert result["employee_name"] is None
    assert "could not reach" in result["reason"]


@respx.mock
async def test_a_connection_failure_is_unverified_rather_than_rejected(keycloak_mode):
    respx.get(f"{HR_BASE}/tw12345").mock(side_effect=httpx.ConnectError("no route"))

    assert (await lookup_user(username="tw12345", subject="me"))["verified"] is False


@respx.mock
async def test_a_non_json_body_is_unverified_rather_than_rejected(keycloak_mode):
    """An HTML error page from a proxy in front of the directory is the directory
    failing to answer, not the directory denying the name."""
    respx.get(f"{HR_BASE}/tw12345").mock(
        return_value=httpx.Response(200, text="<html>502 Bad Gateway</html>")
    )

    assert (await lookup_user(username="tw12345", subject="me"))["verified"] is False
