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
