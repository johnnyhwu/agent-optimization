"""`make preflight` — the OK/FAIL line per seam.

This had no tests, and the gap was not theoretical: the workspace check read a
field off `Workspace` that no longer exists, and nothing caught it because the
only caller is a CLI nobody imports. It is the first thing a developer runs when
pointing this platform at a real agent for the first time, so an `AttributeError`
there is the worst possible first impression — it reads as "this product is
broken" rather than "your agent server is not answering".

What is worth pinning is narrow: that each check produces a line rather than an
exception, and that a reachable-but-unhelpful agent is reported as FAIL with its
own words rather than as a pass.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from app import check_integrations as ci

URL = "https://agent.test"
SKILLS_URL = f"{URL}/skills"


@pytest.fixture
def real_workspace(configure):
    with configure(workspace_impl="real", agent_skills_url=SKILLS_URL, agent_timeout_s=5.0):
        yield


@respx.mock
async def test_a_readable_workspace_passes_and_counts_its_files(real_workspace, capsys):
    respx.get(SKILLS_URL).mock(
        return_value=httpx.Response(200, json={
            "version": "a1b2c3d",
            "skills": {"billing/SKILL.md": "# B", "billing/references/r.md": "# R"},
        })
    )
    assert await ci.check_workspace() is True

    out = capsys.readouterr().out
    assert "OK" in out
    assert "a1b2c3d" in out
    assert "2 skill file(s)" in out


@respx.mock
async def test_an_agent_with_no_skills_still_passes(real_workspace, capsys):
    """A supported configuration, not a failure — and the line has to say so,
    because this is exactly where someone would otherwise go hunting."""
    respx.get(SKILLS_URL).mock(
        return_value=httpx.Response(200, json={"version": "v1", "skills": {}})
    )
    assert await ci.check_workspace() is True
    assert "0 skill file(s)" in capsys.readouterr().out


@respx.mock
async def test_a_derived_version_is_labelled_as_derived(real_workspace, capsys):
    """The two versions carry different guarantees, so the line distinguishes
    them: the agent's own moves on a model change, ours only on a file edit."""
    respx.get(SKILLS_URL).mock(
        return_value=httpx.Response(200, json={"skills": {"a/SKILL.md": "x"}})
    )
    assert await ci.check_workspace() is True
    assert "derived here" in capsys.readouterr().out


@respx.mock
async def test_an_unreachable_agent_fails_with_its_own_words(real_workspace, capsys):
    respx.get(SKILLS_URL).mock(return_value=httpx.Response(404, text="no such route"))
    assert await ci.check_workspace() is False

    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "404" in out


async def test_a_missing_skills_url_is_skipped_and_its_cost_named(configure, capsys):
    """SKIP, not FAIL: an agent with no skills endpoint is a supported one.

    A red line here reported a working deployment as broken. What it does have
    to say is what that configuration gives up, because nothing else on this
    screen will.
    """
    with configure(workspace_impl="real", agent_skills_url=""):
        assert await ci.check_workspace() is True
    out = capsys.readouterr().out
    assert "SKIP" in out
    assert "AGENT_SKILLS_URL" in out
    assert "optimization" in out


async def test_a_fake_seam_is_skipped_not_probed(configure, capsys):
    with configure(workspace_impl="fake"):
        assert await ci.check_workspace() is True
    assert "SKIP" in capsys.readouterr().out


async def test_the_agent_check_is_skipped_when_the_seam_is_fake(configure, capsys):
    with configure(agent_impl="fake"):
        assert await ci.check_agent() is True
    assert "SKIP" in capsys.readouterr().out


async def test_the_agent_check_names_a_missing_chat_url(configure, capsys):
    """FAIL, unlike the skills endpoint above: without this there is no agent."""
    with configure(agent_impl="real", agent_chat_url=""):
        assert await ci.check_agent() is False
    assert "AGENT_CHAT_URL" in capsys.readouterr().out


# --- The preflight under SSO forwarding ----------------------------------
#
# `make preflight` runs from a CLI, where there is no signed-in user. Under
# AGENT_SSO_ENABLED there is therefore no identity to send, and the agent server
# refuses — which is not a broken deployment. Reporting it as FAIL would tell
# whoever just switched the feature on that they had broken their stack.


async def test_the_agent_check_skips_rather_than_failing_under_sso(configure, capsys):
    from app.check_integrations import check_agent

    with configure(
        agent_impl="real",
        agent_chat_url="https://agent.test/v1/chat/completions",
        auth_mode="keycloak",
        agent_sso_enabled=True,
        agent_api_key="",
    ):
        assert await check_agent() is True
    out = capsys.readouterr().out
    assert "SKIP" in out
    assert "no session" in out, "the reason has to name why, or the line is unactionable"


async def test_the_workspace_check_skips_too(configure, capsys):
    from app.check_integrations import check_workspace

    with configure(
        workspace_impl="real",
        agent_skills_url="https://agent.test/skills",
        auth_mode="keycloak",
        agent_sso_enabled=True,
        agent_api_key="",
    ):
        assert await check_workspace() is True
    assert "SKIP" in capsys.readouterr().out


async def test_a_deployment_key_still_gets_a_real_check(configure):
    """The escape hatch again: with a key to send there is something to verify,
    so the preflight must not skip and claim it cannot know."""
    import respx

    from app.check_integrations import check_agent

    with configure(
        agent_impl="real",
        agent_chat_url="https://agent.test/v1/chat/completions",
        auth_mode="keycloak",
        agent_sso_enabled=True,
        agent_api_key="sk-env",
    ):
        with respx.mock:
            respx.post("https://agent.test/v1/chat/completions").mock(
                side_effect=httpx.ConnectError("no route")
            )
            # Reaches the probe rather than skipping — the outcome does not
            # matter here, only that it was attempted.
            assert await check_agent() is False


async def test_sso_off_is_unchanged(configure, capsys):
    """The regression guard: nothing about the switched-off path moved."""
    from app.check_integrations import check_agent

    with configure(agent_impl="fake"):
        assert await check_agent() is True
    assert "AGENT_IMPL=fake" in capsys.readouterr().out
