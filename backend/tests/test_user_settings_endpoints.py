"""The settings endpoints against a real database.

These need one for the same reason `test_optimizer_endpoints.py` does: what is
being protected is partly *what row ends up in the table*, and a stub session
cannot answer that. They skip unless `TEST_DATABASE_URL` is set.

Three things only a database can show:

  * **The baseline.** A brand-new user must not open the settings page to
    twenty-five "new setting" badges. The row is created on their first visit
    with every current key already marked seen, so only keys added *after* that
    visit are ever new. Getting this wrong makes the badge meaningless on day
    one, which is the same as not having it.
  * **The race.** Two tabs loading the page at the same moment both try to
    create that row.
  * **Drift.** A deployment edits `.env`; a user who overrode that key keeps
    winning silently and points at an endpoint that no longer exists. The row
    remembers what the system value was when they set it, so the page can say so.
"""
from __future__ import annotations

import asyncio
import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.routers import user_settings as router
from app.schemas import SeenIn, UserSecretIn, UserSettingsIn
from app.services import user_settings as service

TEST_DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="set TEST_DATABASE_URL to run the database-backed settings tests"
)


@pytest.fixture
async def engine():
    eng = create_async_engine(TEST_DB)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def factory(engine):
    yield async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.execute(text('TRUNCATE TABLE "user_settings" CASCADE'))


@pytest.fixture
async def session(factory):
    async with factory() as s:
        yield s
        await s.rollback()


# --- The baseline -----------------------------------------------------------

async def test_the_first_visit_marks_every_current_key_seen(session):
    first = await router.get_user_settings(session=session, subject="alice")
    assert first["unseen"] == []


async def test_a_key_added_after_the_first_visit_is_unseen(session, monkeypatch):
    await router.get_user_settings(session=session, subject="alice")

    from app import settings_catalog as catalog

    monkeypatch.setattr(
        catalog, "CATALOG", catalog.CATALOG + (catalog.CATALOG[0].replace_key("brand_new"),)
    )
    again = await router.get_user_settings(session=session, subject="alice")
    assert again["unseen"] == ["brand_new"]


async def test_marking_a_key_seen_clears_it(session, monkeypatch):
    from app import settings_catalog as catalog

    monkeypatch.setattr(
        catalog, "CATALOG", catalog.CATALOG + (catalog.CATALOG[0].replace_key("brand_new"),)
    )
    await service.ensure_row(session, "alice", seen=[s.key for s in catalog.CATALOG[:-1]])
    await router.mark_seen(body=SeenIn(keys=["brand_new"]), session=session, subject="alice")
    after = await router.get_user_settings(session=session, subject="alice")
    assert after["unseen"] == []


async def test_a_user_who_never_visited_has_no_row(session):
    """The row is created by opening the settings page, not by loading any page
    that reads defaults — a write on a read path that every page hits is a cost
    with no matching benefit."""
    assert await service.load(session, "never-been-here") == service.EMPTY
    count = await session.scalar(text("SELECT count(*) FROM user_settings"))
    assert count == 0


# --- The race ---------------------------------------------------------------

async def test_two_simultaneous_first_visits_do_not_collide(factory):
    async def visit():
        async with factory() as s:
            return await router.get_user_settings(session=s, subject="alice")

    results = await asyncio.gather(visit(), visit(), return_exceptions=True)
    assert not [r for r in results if isinstance(r, Exception)], results


# --- Writing ----------------------------------------------------------------

async def test_saved_values_come_back(session):
    await router.put_user_settings(
        body=UserSettingsIn(values={"judge_model": "Qwen3-72B"}), session=session, subject="alice"
    )
    read = await router.get_user_settings(session=session, subject="alice")
    assert read["values"]["judge_model"] == "Qwen3-72B"


async def test_a_key_outside_the_catalogue_is_refused(session):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await router.put_user_settings(
            body=UserSettingsIn(values={"script_max_queries": 5000}), session=session, subject="alice"
        )
    assert exc.value.status_code == 400


async def test_saved_values_reach_the_defaults_endpoints(session, configure):
    with configure(judge_model="Qwen3.6-27B"):
        await router.put_user_settings(
            body=UserSettingsIn(values={"judge_model": "Qwen3-72B"}), session=session, subject="alice"
        )
        effective = await service.effective_run_defaults(session, "alice")
        untouched = await service.effective_run_defaults(session, "bob")
    assert effective["judge_model"] == "Qwen3-72B"
    assert untouched["judge_model"] == "Qwen3.6-27B"


async def test_clearing_a_value_restores_the_environment(session, configure):
    with configure(judge_model="Qwen3.6-27B"):
        await router.put_user_settings(
            body=UserSettingsIn(values={"judge_model": "Qwen3-72B"}), session=session, subject="alice"
        )
        await router.put_user_settings(
            body=UserSettingsIn(values={}), session=session, subject="alice"
        )
        effective = await service.effective_run_defaults(session, "alice")
    assert effective["judge_model"] == "Qwen3.6-27B"


async def test_identities_differing_only_in_case_are_one_user(session):
    await router.put_user_settings(
        body=UserSettingsIn(values={"judge_model": "Qwen3-72B"}), session=session, subject="Alice"
    )
    read = await router.get_user_settings(session=session, subject="alice")
    assert read["values"]["judge_model"] == "Qwen3-72B"


# --- Drift ------------------------------------------------------------------

async def test_a_changed_system_value_is_reported(session, configure):
    with configure(llm_base_url="http://old-llm"):
        await router.put_user_settings(
            body=UserSettingsIn(values={"llm_base_url": "http://mine"}),
            session=session, subject="alice",
        )
    with configure(llm_base_url="http://new-llm"):
        read = await router.get_user_settings(session=session, subject="alice")
    assert read["drifted"] == [
        {"key": "llm_base_url", "was": "http://old-llm", "now": "http://new-llm"}
    ]


async def test_an_unchanged_system_value_is_not_reported(session, configure):
    with configure(llm_base_url="http://llm"):
        await router.put_user_settings(
            body=UserSettingsIn(values={"llm_base_url": "http://mine"}),
            session=session, subject="alice",
        )
        read = await router.get_user_settings(session=session, subject="alice")
    assert read["drifted"] == []


# --- Credentials never come out ---------------------------------------------

async def test_no_response_carries_a_credential(session, configure):
    KEY = "kBv6H0kQ2p6b0iC5j3RiUcDnJ5c1RzOaTQnUvGZlp1U="
    with configure(settings_secret_key=KEY, auth_mode="keycloak"):
        await router.put_user_secret(
            key="llm_api_key",
            body=UserSecretIn(value="sk-live-1234", endpoint="http://llm"),
            session=session, subject="alice",
        )
        read = await router.get_user_settings(session=session, subject="alice")
    assert "sk-live-1234" not in str(read)
    assert read["secrets"]["llm_api_key"]["set"] is True


async def test_deleting_a_credential_removes_the_row_contents(session, configure):
    KEY = "kBv6H0kQ2p6b0iC5j3RiUcDnJ5c1RzOaTQnUvGZlp1U="
    with configure(settings_secret_key=KEY, auth_mode="keycloak"):
        await router.put_user_secret(
            key="llm_api_key",
            body=UserSecretIn(value="sk-live-1234", endpoint="http://llm"),
            session=session, subject="alice",
        )
        await router.delete_user_secret(
            key="llm_api_key", session=session, subject="alice"
        )
        read = await router.get_user_settings(session=session, subject="alice")
    stored = await session.scalar(text("SELECT secrets::text FROM user_settings"))
    assert read["secrets"]["llm_api_key"]["set"] is False
    assert "llm_api_key" not in stored


# --- Status -----------------------------------------------------------------

async def test_status_is_cheap_and_says_what_needs_attention(session, configure):
    with configure(llm_base_url="http://old-llm"):
        await router.put_user_settings(
            body=UserSettingsIn(values={"llm_base_url": "http://mine"}),
            session=session, subject="alice",
        )
    with configure(llm_base_url="http://new-llm"):
        status = await router.get_status(session=session, subject="alice")
    assert status == {"unseen": 0, "drifted": 1}
