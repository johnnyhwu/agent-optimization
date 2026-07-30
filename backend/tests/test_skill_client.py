"""Real SkillClient: the two agent-server endpoints the playground reads (§10.2).

The theme of these tests is that an unreadable catalogue must be loud. A skill the
developer cannot load is a starting point silently lost — they will paste from
memory instead — so "the agent has no skills" and "your URL is wrong" have to be
distinguishable, and only the first one is an empty list.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from app.integrations.real.skills import HttpSkillClient, SkillFetchError

URL = "https://agent.test"
SKILLS_URL = f"{URL}/skills"


@pytest.fixture
def client(configure):
    with configure(agent_base_url=URL, agent_timeout_s=5.0):
        yield HttpSkillClient()


@respx.mock
async def test_lists_wrapped_catalogue(client):
    respx.get(SKILLS_URL).mock(return_value=httpx.Response(200, json={
        "skills": [
            {"name": "billing", "description": "invoices"},
            {"name": "reporting"},
        ]
    }))
    skills = await client.list_skills()

    assert [s.name for s in skills] == ["billing", "reporting"]
    assert skills[0].description == "invoices"
    assert skills[1].description is None


@respx.mock
async def test_lists_bare_array_and_plain_names(client):
    """Tolerant on shape: this platform does not own the agent server."""
    respx.get(SKILLS_URL).mock(
        return_value=httpx.Response(200, json=["billing", {"skill": "reporting"}])
    )
    assert [s.name for s in await client.list_skills()] == ["billing", "reporting"]


@respx.mock
async def test_empty_catalogue_is_not_an_error(client):
    respx.get(SKILLS_URL).mock(return_value=httpx.Response(200, json={"skills": []}))
    assert await client.list_skills() == []


@respx.mock
async def test_unusable_catalogue_raises_with_the_body(client):
    """A 200 whose body is not a catalogue is a failure, not an empty list."""
    respx.get(SKILLS_URL).mock(
        return_value=httpx.Response(200, json={"unexpected": "shape"})
    )
    with pytest.raises(SkillFetchError) as exc:
        await client.list_skills()
    assert "unexpected" in str(exc.value)


@respx.mock
async def test_entries_without_names_raise(client):
    respx.get(SKILLS_URL).mock(
        return_value=httpx.Response(200, json={"skills": [{"description": "no name"}]})
    )
    with pytest.raises(SkillFetchError):
        await client.list_skills()


@respx.mock
async def test_http_error_carries_status_and_body(client):
    respx.get(SKILLS_URL).mock(return_value=httpx.Response(404, text="no such route"))
    with pytest.raises(SkillFetchError) as exc:
        await client.list_skills()
    assert "404" in str(exc.value)
    assert "no such route" in str(exc.value)


@respx.mock
async def test_transport_error_names_the_host(client):
    respx.get(SKILLS_URL).mock(side_effect=httpx.ConnectError("nope"))
    with pytest.raises(SkillFetchError) as exc:
        await client.list_skills()
    assert URL in str(exc.value)


@respx.mock
async def test_gets_one_skill(client):
    respx.get(f"{SKILLS_URL}/billing").mock(return_value=httpx.Response(200, json={
        "name": "billing", "content": "# Billing\n1. ...", "description": "invoices",
    }))
    skill = await client.get_skill("billing")

    assert skill.name == "billing"
    assert skill.content.startswith("# Billing")
    assert skill.description == "invoices"


@respx.mock
async def test_skill_text_under_an_alternative_key(client):
    respx.get(f"{SKILLS_URL}/billing").mock(
        return_value=httpx.Response(200, json={"text": "# Billing"})
    )
    assert (await client.get_skill("billing")).content == "# Billing"


@respx.mock
async def test_skill_served_as_plain_text(client):
    respx.get(f"{SKILLS_URL}/billing").mock(
        return_value=httpx.Response(200, text="# Billing\nplain markdown")
    )
    assert (await client.get_skill("billing")).content.startswith("# Billing")


@respx.mock
async def test_skill_without_text_raises(client):
    respx.get(f"{SKILLS_URL}/billing").mock(
        return_value=httpx.Response(200, json={"name": "billing"})
    )
    with pytest.raises(SkillFetchError):
        await client.get_skill("billing")


async def test_missing_base_url_says_where_to_set_it(configure):
    with configure(agent_base_url=""):
        with pytest.raises(RuntimeError) as exc:
            HttpSkillClient()
    assert "AGENT_BASE_URL" in str(exc.value)


@respx.mock
async def test_per_attempt_base_url_overrides_the_environment(configure):
    """An attempt can point at a different agent server than the environment,
    the same way a run can (§9.15)."""
    other = "https://other-agent.test"
    respx.get(f"{other}/skills").mock(
        return_value=httpx.Response(200, json={"skills": [{"name": "x"}]})
    )
    with configure(agent_base_url=URL):
        client = HttpSkillClient(base_url=other)
        assert [s.name for s in await client.list_skills()] == ["x"]
