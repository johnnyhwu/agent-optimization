"""Real WorkspaceClient: the one agent-server endpoint the platform reads.

The theme of these tests is that an unreadable workspace must be loud. A skill the
developer cannot load is a starting point silently lost — they will paste from
memory instead — so "the agent has no skills" and "your URL is wrong" have to be
distinguishable, and only the first one is an empty map.

The second theme is the version. It is optional on the wire because asking every
agent author to maintain a string that changes whenever anything behavioural
changes is the single most forgettable requirement in the contract — and a
version that silently stops moving disables the staleness check without saying
so. When it is absent the platform derives one from the skill files, which is
weaker (it cannot see a model or prompt change) but never wrong about what it
does cover.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from app.integrations.real.workspace import HttpWorkspaceClient, WorkspaceFetchError

URL = "https://agent.test"
SKILLS_URL = f"{URL}/skills"

FULL_BODY = {
    "version": "a1b2c3d",
    "skills": {
        "billing/SKILL.md": "# Billing\n1. ...",
        "billing/references/refunds.md": "# Refunds\n...",
    },
}


@pytest.fixture
def client(configure):
    with configure(agent_base_url=URL, agent_timeout_s=5.0):
        yield HttpWorkspaceClient()


@respx.mock
async def test_reads_the_skill_files(client):
    respx.get(SKILLS_URL).mock(return_value=httpx.Response(200, json=FULL_BODY))
    ws = await client.get_workspace()

    assert ws.version == "a1b2c3d"
    # A skill is a directory: the reference file arrives as its own entry, which
    # is precisely what the old one-string-per-skill model could not express.
    assert set(ws.skills) == {"billing/SKILL.md", "billing/references/refunds.md"}
    assert ws.skills["billing/SKILL.md"] == "# Billing\n1. ..."


@respx.mock
async def test_the_servers_own_version_is_preferred_over_a_derived_one(client):
    """It can see more than we can: a model or prompt change moves it too."""
    respx.get(SKILLS_URL).mock(return_value=httpx.Response(200, json=FULL_BODY))
    assert (await client.get_workspace()).version == "a1b2c3d"


@respx.mock
async def test_a_missing_version_is_derived_from_the_skills(client):
    """A server that does not version itself still gets a staleness check,
    covering the half of its behaviour we can actually see."""
    respx.get(SKILLS_URL).mock(
        return_value=httpx.Response(200, json={"skills": {"a/SKILL.md": "x"}})
    )
    version = (await client.get_workspace()).version

    # Non-empty, because "" is what every consumer reads as "no check possible".
    assert version
    assert version.startswith("sha256.")


@respx.mock
async def test_a_derived_version_is_stable_for_the_same_skills(client):
    """Otherwise every send would look like a change and block on staleness."""
    respx.get(SKILLS_URL).mock(
        return_value=httpx.Response(200, json={"skills": {"b/SKILL.md": "y", "a/SKILL.md": "x"}})
    )
    first = (await client.get_workspace()).version

    respx.get(SKILLS_URL).mock(
        # Same content, different key order on the wire.
        return_value=httpx.Response(200, json={"skills": {"a/SKILL.md": "x", "b/SKILL.md": "y"}})
    )
    assert (await client.get_workspace()).version == first


@respx.mock
async def test_a_derived_version_moves_when_a_skill_moves(client):
    respx.get(SKILLS_URL).mock(
        return_value=httpx.Response(200, json={"skills": {"a/SKILL.md": "x"}})
    )
    before = (await client.get_workspace()).version

    respx.get(SKILLS_URL).mock(
        return_value=httpx.Response(200, json={"skills": {"a/SKILL.md": "x edited"}})
    )
    assert (await client.get_workspace()).version != before


@respx.mock
async def test_an_empty_workspace_is_not_an_error(client):
    """An agent with no skills is a legitimate answer, unlike a failure. Both
    Evaluation and the playground have to stay usable against one."""
    respx.get(SKILLS_URL).mock(
        return_value=httpx.Response(200, json={"version": "v1", "skills": {}})
    )
    ws = await client.get_workspace()

    assert ws.skills == {}
    assert ws.version == "v1"


@respx.mock
async def test_skills_omitted_entirely_is_an_empty_workspace(client):
    """The key is required by the contract, but a server that omits it is
    saying "none" — which is a legitimate state, not a broken body."""
    respx.get(SKILLS_URL).mock(return_value=httpx.Response(200, json={"version": "v1"}))
    assert (await client.get_workspace()).skills == {}


@respx.mock
async def test_null_skills_is_an_empty_workspace(client):
    respx.get(SKILLS_URL).mock(
        return_value=httpx.Response(200, json={"version": "v1", "skills": None})
    )
    assert (await client.get_workspace()).skills == {}


@respx.mock
async def test_skills_that_are_not_path_to_text_raise(client):
    """A 200 whose skills are the wrong shape is a failure, not an empty map."""
    respx.get(SKILLS_URL).mock(
        return_value=httpx.Response(200, json={"skills": {"a/SKILL.md": 42}})
    )
    with pytest.raises(WorkspaceFetchError) as exc:
        await client.get_workspace()
    assert "a/SKILL.md" in str(exc.value)


@respx.mock
async def test_skills_as_a_list_raises(client):
    respx.get(SKILLS_URL).mock(return_value=httpx.Response(200, json={"skills": ["billing"]}))
    with pytest.raises(WorkspaceFetchError):
        await client.get_workspace()


@respx.mock
async def test_a_non_object_body_raises(client):
    respx.get(SKILLS_URL).mock(return_value=httpx.Response(200, json=["billing"]))
    with pytest.raises(WorkspaceFetchError):
        await client.get_workspace()


@respx.mock
async def test_http_error_carries_status_and_body(client):
    respx.get(SKILLS_URL).mock(return_value=httpx.Response(404, text="no such route"))
    with pytest.raises(WorkspaceFetchError) as exc:
        await client.get_workspace()
    assert "404" in str(exc.value)
    assert "no such route" in str(exc.value)


@respx.mock
async def test_the_error_names_the_endpoint_it_tried(client):
    """"/skills 404s" and "the host is down" send a developer to different
    places, so the message has to say which one happened."""
    respx.get(SKILLS_URL).mock(return_value=httpx.Response(404, text="nope"))
    with pytest.raises(WorkspaceFetchError) as exc:
        await client.get_workspace()
    assert "/skills" in str(exc.value)


@respx.mock
async def test_non_json_body_raises_rather_than_being_guessed_at(client):
    respx.get(SKILLS_URL).mock(return_value=httpx.Response(200, text="<html>nope</html>"))
    with pytest.raises(WorkspaceFetchError) as exc:
        await client.get_workspace()
    assert "nope" in str(exc.value)


@respx.mock
async def test_transport_error_names_the_host(client):
    respx.get(SKILLS_URL).mock(side_effect=httpx.ConnectError("nope"))
    with pytest.raises(WorkspaceFetchError) as exc:
        await client.get_workspace()
    assert URL in str(exc.value)


@respx.mock
async def test_get_version_comes_from_the_same_read(client):
    """There is no version endpoint any more: "has it moved?" is answered by
    the same body that answers "what is it?", so the two can never disagree."""
    route = respx.get(SKILLS_URL).mock(return_value=httpx.Response(200, json=FULL_BODY))
    assert await client.get_version() == "a1b2c3d"
    assert route.called


@respx.mock
async def test_get_version_derives_one_when_the_server_gives_none(client):
    respx.get(SKILLS_URL).mock(
        return_value=httpx.Response(200, json={"skills": {"a/SKILL.md": "x"}})
    )
    assert (await client.get_version()).startswith("sha256.")


@respx.mock
async def test_get_version_propagates_a_failure(client):
    """A version check that swallowed a 404 would report "unchanged" forever."""
    respx.get(SKILLS_URL).mock(return_value=httpx.Response(404, text="gone"))
    with pytest.raises(WorkspaceFetchError):
        await client.get_version()


def test_no_base_url_is_a_readable_error(configure):
    with configure(agent_base_url=""):
        with pytest.raises(RuntimeError) as exc:
            HttpWorkspaceClient()
    assert "AGENT_BASE_URL" in str(exc.value)
