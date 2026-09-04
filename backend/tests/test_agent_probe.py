"""The four connection checks, and the layering that keeps them separable.

The interesting behaviour here is not "does it call the agent" — it is which
outcome each failure produces. Three screens gate on these results with
different strictness, and they can only do that if `chat`, `override` and
`trace` never collapse into one another.
"""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
import respx

from app.integrations.base import NOT_READY, Trace, Workspace
from app.services import agent_probe
from app.services.agent_probe import (
    PROBE_SKILL_PATH,
    make_probe_skill,
    probe_chat,
    probe_skills,
)

CHAT_URL = "https://agent.test/v1/chat/completions"
SKILLS_URL = "https://agent.test/skills"


def completion(text: str) -> dict:
    return {
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ]
    }


@pytest.fixture
def real(configure):
    with configure(
        agent_impl="real", workspace_impl="real",
        agent_chat_url=CHAT_URL, agent_skills_url=SKILLS_URL,
        agent_timeout_s=30.0, agent_probe_timeout_s=5.0,
    ):
        yield


# --- The probe skill --------------------------------------------------------

def test_the_magic_value_is_different_every_time():
    """A constant would eventually be hard-coded to make the check pass.

    A check that can be satisfied without reading the file we just sent is not
    a check — it is a way for an agent that ignores the override to look
    compliant, which is the exact failure this exists to catch.
    """
    _, _, first = make_probe_skill()
    _, _, second = make_probe_skill()
    assert first != second


def test_the_probe_skill_carries_the_value_and_frontmatter():
    skills, question, magic = make_probe_skill()
    body = skills[PROBE_SKILL_PATH]
    assert magic in body
    # Frontmatter, because agents that route by description need one to load it
    # at all — without it the probe would fail against a working agent.
    assert body.startswith("---\n")
    assert "skill_studio_probe" in question


# --- skills -----------------------------------------------------------------

@respx.mock
async def test_a_readable_skills_endpoint_passes(real):
    respx.get(SKILLS_URL).mock(
        return_value=httpx.Response(200, json={
            "version": "a1b2c3d",
            "skills": {"billing/SKILL.md": "# Billing"},
        })
    )
    result = await probe_skills(SKILLS_URL)

    assert result.skills.ok is True
    assert result.version == "a1b2c3d"
    assert result.paths == ["billing/SKILL.md"]


@respx.mock
async def test_the_response_preview_holds_sizes_not_file_bodies(real):
    """A workspace is routinely hundreds of kilobytes.

    None of it helps someone checking a connection, and all of it would travel
    twice — once as the listing, once inside the preview shown beside the field.
    """
    respx.get(SKILLS_URL).mock(
        return_value=httpx.Response(200, json={
            "version": "v1", "skills": {"billing/SKILL.md": "x" * 5000},
        })
    )
    result = await probe_skills(SKILLS_URL)

    assert "x" * 100 not in result.response_preview
    assert "5000 chars" in result.response_preview


@respx.mock
async def test_an_unreachable_skills_endpoint_fails_with_its_own_words(real):
    respx.get(SKILLS_URL).mock(return_value=httpx.Response(404, text="no such route"))
    result = await probe_skills(SKILLS_URL)

    assert result.skills.ok is False
    assert "404" in result.skills.error


async def test_no_skills_url_is_not_attempted_rather_than_failed(configure):
    """The entry-level tier. `ok=None`, and nothing red anywhere."""
    with configure(workspace_impl="real", agent_skills_url=""):
        result = await probe_skills("")

    assert result.skills.ok is None
    assert result.skills.error == ""


async def test_an_empty_workspace_is_a_pass_not_a_failure(configure, monkeypatch):
    """"This agent has no skills" is a warning about the eval set, not a fault.

    Collapsing it into a failure is what makes a developer retype a skill they
    never lost.
    """
    class Empty:
        async def get_workspace(self):
            return Workspace(version="v1", skills={})

    monkeypatch.setattr(
        agent_probe, "build_seams",
        lambda *a, **k: SimpleNamespace(workspace=Empty()),
    )
    with configure(workspace_impl="real"):
        result = await probe_skills(SKILLS_URL)

    assert result.skills.ok is True
    assert result.paths == []


# --- chat and override ------------------------------------------------------

@respx.mock
async def test_an_agent_that_applies_the_override_passes_both(real, monkeypatch):
    magic = {}
    original = agent_probe.make_probe_skill

    def capture():
        skills, question, value = original()
        magic["value"] = value
        return skills, question, value

    monkeypatch.setattr(agent_probe, "make_probe_skill", capture)
    respx.post(CHAT_URL).mock(
        side_effect=lambda request: httpx.Response(
            200, json=completion(f"The magic value is {magic['value']}.")
        )
    )
    result = await probe_chat(CHAT_URL)

    assert result.chat.ok is True
    assert result.override.ok is True
    # Not asked for, so not attempted — and it must not read as a failure.
    assert result.trace.ok is None


@respx.mock
async def test_an_answer_without_the_magic_value_fails_only_the_override(real):
    """The layering that lets three screens gate differently on one call.

    An agent that answers is a working chat endpoint even when the override did
    not land: the playground still lets you ask it questions, and only the
    wizard refuses. Failing `chat` here would take both away.
    """
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(200, json=completion("I have no idea."))
    )
    result = await probe_chat(CHAT_URL)

    assert result.chat.ok is True
    assert result.override.ok is False
    assert "override" in result.override.error


@respx.mock
async def test_the_override_failure_does_not_accuse(real):
    """A refusal, an unloaded tool and a comment-stripping prompt pipeline all
    land here too. Naming only the first sends people to fix the wrong thing."""
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(200, json=completion("I cannot help with that."))
    )
    result = await probe_chat(CHAT_URL)

    assert "declined" in result.override.error
    assert "dropped" in result.override.error


@respx.mock
async def test_a_dead_endpoint_fails_chat_and_leaves_override_unattempted(real):
    respx.post(CHAT_URL).mock(side_effect=httpx.ConnectError("connection refused"))
    result = await probe_chat(CHAT_URL)

    assert result.chat.ok is False
    # Nothing was learned about the override, and saying otherwise would send
    # someone to fix an override on an agent that is simply not running.
    assert result.override.ok is None


@respx.mock
async def test_an_empty_answer_fails_chat(real):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=completion("")))
    assert (await probe_chat(CHAT_URL)).chat.ok is False


@respx.mock
async def test_without_an_override_nothing_is_claimed_about_it(real):
    """The cheap "is anything listening" form, used by the startup check."""
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=completion("ok")))
    result = await probe_chat(CHAT_URL, with_override=False)

    assert result.chat.ok is True
    assert result.override.ok is None
    assert PROBE_SKILL_PATH not in result.request_preview


@respx.mock
async def test_the_request_preview_is_the_body_that_was_sent(real):
    """Shown rather than described.

    An implementer reading the real bytes finds a field-name mismatch in
    seconds; the same mismatch hides in a prose spec for an afternoon.
    """
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=completion("ok")))
    result = await probe_chat(CHAT_URL)

    assert "skill_studio" in result.request_preview
    assert PROBE_SKILL_PATH in result.request_preview
    assert result.trace_id in result.request_preview


# --- trace ------------------------------------------------------------------

@respx.mock
async def test_a_trace_that_lands_passes(real):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=completion("ok")))

    class Store:
        async def fetch_trace(self, trace_id):
            return Trace(correlation_id=trace_id, spans=[])

    result = await probe_chat(CHAT_URL, with_override=False, trace_client=Store())
    assert result.trace.ok is True


@respx.mock
async def test_a_trace_that_is_late_is_retried_before_it_is_failed(real, monkeypatch):
    """Ingestion is asynchronous, so the first read after a call routinely misses.

    That delay is this check's only false negative, and it is the one people
    would hit most — reporting it without retrying would teach everyone to
    ignore the check.
    """
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=completion("ok")))
    monkeypatch.setattr(agent_probe, "TRACE_RETRY_DELAY_S", 0)

    calls = {"n": 0}

    class Slow:
        async def fetch_trace(self, trace_id):
            calls["n"] += 1
            if calls["n"] < 2:
                return NOT_READY
            return Trace(correlation_id=trace_id, spans=[])

    result = await probe_chat(CHAT_URL, with_override=False, trace_client=Slow())

    assert result.trace.ok is True
    assert calls["n"] == 2


@respx.mock
async def test_a_trace_that_never_lands_names_both_causes(real, monkeypatch):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=completion("ok")))
    monkeypatch.setattr(agent_probe, "TRACE_RETRY_DELAY_S", 0)

    class Never:
        async def fetch_trace(self, trace_id):
            return NOT_READY

    result = await probe_chat(CHAT_URL, with_override=False, trace_client=Never())

    assert result.trace.ok is False
    # The agent reusing our id is the requirement; "nothing was written" is the
    # other half, and a developer cannot tell which without being told both.
    assert "trace id" in result.trace.error


@respx.mock
async def test_an_unreachable_trace_store_is_its_own_sentence(real, monkeypatch):
    """"Langfuse is down" and "the agent invented its own id" are different jobs."""
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=completion("ok")))
    monkeypatch.setattr(agent_probe, "TRACE_RETRY_DELAY_S", 0)

    class Broken:
        async def fetch_trace(self, trace_id):
            raise RuntimeError("401 from Langfuse")

    result = await probe_chat(CHAT_URL, with_override=False, trace_client=Broken())

    assert result.trace.ok is False
    assert "trace store" in result.trace.error


@respx.mock
async def test_a_failed_call_is_never_reported_as_a_missing_trace(real):
    """No call, no trace — and blaming the trace store for it wastes an hour."""
    respx.post(CHAT_URL).mock(side_effect=httpx.ConnectError("refused"))

    class Store:
        async def fetch_trace(self, trace_id):  # pragma: no cover - must not run
            raise AssertionError("the trace store must not be asked about a dead call")

    result = await probe_chat(CHAT_URL, with_override=False, trace_client=Store())

    assert result.chat.ok is False
    assert result.trace.ok is None


# --- seams ------------------------------------------------------------------

async def test_a_fake_deployment_probes_its_fake_seams(configure):
    """Reaching past the seam would make the demo report a connection failure
    against an agent it was never supposed to have."""
    with configure(agent_impl="fake", workspace_impl="fake"):
        skills = await probe_skills("")
        chat = await probe_chat("", with_override=False)

    assert skills.skills.ok is True
    assert isinstance(chat.chat.ok, bool)


@respx.mock
async def test_the_probe_uses_the_url_it_was_given_not_the_environment(real):
    other = "https://agent-b.test/chat"
    respx.post(other).mock(return_value=httpx.Response(200, json=completion("ok")))
    result = await probe_chat(other, with_override=False)

    assert result.chat.ok is True
    assert str(respx.calls[0].request.url) == other
