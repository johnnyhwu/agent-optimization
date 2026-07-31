"""Real WorkspaceClient: the two agent-server endpoints the playground reads (§10.2).

The theme of these tests is that an unreadable workspace must be loud. A skill the
developer cannot load is a starting point silently lost — they will paste from
memory instead — so "the agent has no skills" and "your URL is wrong" have to be
distinguishable, and only the first one is an empty map.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from app.integrations.real.workspace import HttpWorkspaceClient, WorkspaceFetchError

URL = "https://agent.test"
WORKSPACE_URL = f"{URL}/get_workspace"
VERSION_URL = f"{URL}/get_config_version"

FULL_BODY = {
    "version": "a1b2c3d",
    "config": {
        "agents": {"defaults": {"model": "gpt-4o", "temperature": 0.2}},
        "retries": 3,
    },
    "redacted_paths": ["agents.defaults.api_key"],
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
async def test_reads_the_whole_workspace(client):
    respx.get(WORKSPACE_URL).mock(return_value=httpx.Response(200, json=FULL_BODY))
    ws = await client.get_workspace()

    assert ws.version == "a1b2c3d"
    assert ws.config["agents"]["defaults"]["model"] == "gpt-4o"
    assert ws.redacted_paths == ["agents.defaults.api_key"]
    # A skill is a directory: the reference file arrives as its own entry, which
    # is precisely what the old one-string-per-skill model could not express.
    assert set(ws.skills) == {"billing/SKILL.md", "billing/references/refunds.md"}


@respx.mock
async def test_config_arrives_nested_not_flattened(client):
    """The UI labels a field by its path, so the hierarchy has to survive."""
    respx.get(WORKSPACE_URL).mock(return_value=httpx.Response(200, json=FULL_BODY))
    ws = await client.get_workspace()

    assert isinstance(ws.config["agents"], dict)
    assert "agents.defaults.model" not in ws.config


@respx.mock
async def test_empty_workspace_is_not_an_error(client):
    """An agent with no skills yet is a legitimate answer, unlike a failure."""
    respx.get(WORKSPACE_URL).mock(
        return_value=httpx.Response(200, json={"version": "v1", "config": {}, "skills": {}})
    )
    ws = await client.get_workspace()

    assert ws.skills == {}
    assert ws.config == {}
    assert ws.redacted_paths == []


@respx.mock
async def test_missing_version_still_yields_a_workspace(client):
    """A server that does not version its workspace loses the staleness check,
    not the ability to be edited."""
    respx.get(WORKSPACE_URL).mock(
        return_value=httpx.Response(200, json={"config": {}, "skills": {"a/SKILL.md": "x"}})
    )
    assert (await client.get_workspace()).version == ""


@respx.mock
async def test_non_object_config_raises_with_the_body(client):
    respx.get(WORKSPACE_URL).mock(
        return_value=httpx.Response(200, json={"config": "not an object", "skills": {}})
    )
    with pytest.raises(WorkspaceFetchError) as exc:
        await client.get_workspace()
    assert "not an object" in str(exc.value)


@respx.mock
async def test_skills_that_are_not_path_to_text_raise(client):
    """A 200 whose skills are the wrong shape is a failure, not an empty map."""
    respx.get(WORKSPACE_URL).mock(
        return_value=httpx.Response(200, json={"config": {}, "skills": {"a/SKILL.md": 42}})
    )
    with pytest.raises(WorkspaceFetchError) as exc:
        await client.get_workspace()
    assert "a/SKILL.md" in str(exc.value)


@respx.mock
async def test_skills_as_a_list_raises(client):
    respx.get(WORKSPACE_URL).mock(
        return_value=httpx.Response(200, json={"config": {}, "skills": ["billing"]})
    )
    with pytest.raises(WorkspaceFetchError):
        await client.get_workspace()


@respx.mock
async def test_http_error_carries_status_and_body(client):
    respx.get(WORKSPACE_URL).mock(return_value=httpx.Response(404, text="no such route"))
    with pytest.raises(WorkspaceFetchError) as exc:
        await client.get_workspace()
    assert "404" in str(exc.value)
    assert "no such route" in str(exc.value)


@respx.mock
async def test_non_json_body_raises_rather_than_being_guessed_at(client):
    respx.get(WORKSPACE_URL).mock(return_value=httpx.Response(200, text="<html>nope</html>"))
    with pytest.raises(WorkspaceFetchError) as exc:
        await client.get_workspace()
    assert "nope" in str(exc.value)


@respx.mock
async def test_transport_error_names_the_host(client):
    respx.get(WORKSPACE_URL).mock(side_effect=httpx.ConnectError("nope"))
    with pytest.raises(WorkspaceFetchError) as exc:
        await client.get_workspace()
    assert URL in str(exc.value)


@respx.mock
async def test_reads_the_version_on_its_own(client):
    respx.get(VERSION_URL).mock(
        return_value=httpx.Response(200, json={"version": "a1b2c3d-dirty.9f3e11c"})
    )
    assert await client.get_version() == "a1b2c3d-dirty.9f3e11c"


@respx.mock
async def test_version_without_a_version_raises(client):
    """Silently returning "" would read as "unchanged" and defeat the check."""
    respx.get(VERSION_URL).mock(return_value=httpx.Response(200, json={"commit": "abc"}))
    with pytest.raises(WorkspaceFetchError):
        await client.get_version()


def test_no_base_url_is_a_readable_error(configure):
    with configure(agent_base_url=""):
        with pytest.raises(RuntimeError) as exc:
            HttpWorkspaceClient()
    assert "AGENT_BASE_URL" in str(exc.value)
