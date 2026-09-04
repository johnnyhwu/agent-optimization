"""The acceptance checklist, run against one agent server.

The three cases worth their own tests are the three that ordinary use never
exercises — an empty skills map, a traversing path, an override that outlives
its request. Each of them produces a correct-looking answer when it is wrong,
which is exactly why they need a checker rather than a code review.
"""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.services.agent_conformance import run_conformance

CHAT_URL = "https://agent.test/v1/chat/completions"
SKILLS_URL = "https://agent.test/skills"


def completion(text: str) -> dict:
    return {
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": text},
             "finish_reason": "stop"}
        ]
    }


def case(report, cid):
    return next(c for c in report.cases if c.id == cid)


def ids(report):
    return [c.id for c in report.cases]


class FakeAgent:
    """An agent server that behaves correctly, with switches for each defect.

    Written as one handler rather than several mocks because the defects are
    *interactions* — a persisted override is only visible on the call after the
    one that carried it.
    """

    def __init__(self, *, applies_override=True, empty_means_own=False,
                 persists=False, allows_traversal=False, rejects_unknown=False):
        self.applies_override = applies_override
        self.empty_means_own = empty_means_own
        self.persists = persists
        self.allows_traversal = allows_traversal
        self.rejects_unknown = rejects_unknown
        self.leaked = None  # what a persisted override left behind

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        vendor = body.get("skill_studio") or {}
        skills = vendor.get("skills")

        if skills is not None and any(".." in k for k in skills):
            if not self.allows_traversal:
                return httpx.Response(400, text="unsafe skill path")
            return httpx.Response(200, json=completion("ok"))

        if any(k not in {"timeout_s", "trace_data", "skills"} for k in vendor):
            if self.rejects_unknown:
                return httpx.Response(422, text="unknown field")

        # Which files this call sees, by the contract's three states.
        if skills is None:
            effective = self.leaked or {}
        elif skills == {}:
            effective = (self.leaked or {}) if self.empty_means_own else {}
        else:
            effective = skills
            if self.persists:
                self.leaked = skills

        question = next(
            (m["content"] for m in reversed(body["messages"]) if m["role"] == "user"),
            "",
        )

        text = "ok"
        # Only when *asked*. A fake that recited the magic value whatever it was
        # asked would let a persistence check pass while sending the wrong
        # question — which is exactly the bug this file is written to catch.
        if effective and self.applies_override and "magic value" in question:
            for content in effective.values():
                for word in content.split():
                    if word.startswith("XYZZY-"):
                        text = f"The magic value is {word.rstrip('.')}"
        return httpx.Response(200, json=completion(text))


def mount(agent: FakeAgent):
    respx.post(CHAT_URL).mock(side_effect=agent)
    respx.get(SKILLS_URL).mock(
        return_value=httpx.Response(
            200, json={"version": "a1b2c3d", "skills": {"billing/SKILL.md": "# B"}}
        )
    )


@pytest.fixture
def real(configure):
    with configure(
        agent_impl="real", workspace_impl="real",
        agent_chat_url=CHAT_URL, agent_skills_url=SKILLS_URL,
        agent_timeout_s=30.0, agent_probe_timeout_s=5.0,
    ):
        yield


@respx.mock
async def test_a_correct_server_passes_everything(real):
    mount(FakeAgent())
    report = await run_conformance(CHAT_URL, SKILLS_URL)

    failed = [c.id for c in report.cases if c.result.ok is False]
    assert failed == []
    assert report.tier == 2


@respx.mock
async def test_an_unreachable_server_stops_after_the_first_case(real):
    """No point testing an override against something that is not running — and
    six failures where there is one problem is a worse report than one."""
    respx.post(CHAT_URL).mock(side_effect=httpx.ConnectError("refused"))
    report = await run_conformance(CHAT_URL, SKILLS_URL)

    assert ids(report) == ["chat"]
    assert report.tier == 0
    assert "did not answer" in report.summary


@respx.mock
async def test_an_ignored_override_is_caught(real):
    mount(FakeAgent(applies_override=False))
    report = await run_conformance(CHAT_URL, SKILLS_URL)

    assert case(report, "override").result.ok is False
    # Tier 1, not 0: this agent is perfectly usable for evaluation and the
    # playground. Only optimization is out of reach.
    assert report.tier == 1
    assert "Optimization needs" in report.summary


@respx.mock
async def test_an_empty_map_treated_as_use_your_own_is_caught(real):
    """The falsy-`{}` bug, which nothing else can see.

    The agent answers correctly, from its own files, and the only symptom is
    that a measurement against no skill at all silently measured the deployed
    skill instead.
    """
    mount(FakeAgent(empty_means_own=True, persists=True))
    report = await run_conformance(CHAT_URL, SKILLS_URL)

    assert case(report, "empty_skills").result.ok is False
    assert "did not clear" in case(report, "empty_skills").result.error


@respx.mock
async def test_a_persisted_override_is_caught(real):
    """Visible only across two calls: the second one still knows the first's
    files, which means the deployed agent has been changed for everyone.

    The second call has to ask the question the leak would answer. Re-sending
    the harmless baseline question instead proved nothing — no agent replies to
    "reply with the single word: ok" by quoting a skill — so the case passed
    whether or not anything had been written to disk.
    """
    mount(FakeAgent(persists=True))
    report = await run_conformance(CHAT_URL, SKILLS_URL)

    assert case(report, "not_persisted").result.ok is False
    assert "outlived" in case(report, "not_persisted").result.error


@respx.mock
async def test_an_accepted_traversing_path_is_caught(real):
    mount(FakeAgent(allows_traversal=True))
    report = await run_conformance(CHAT_URL, SKILLS_URL)

    assert case(report, "path_safety").result.ok is False
    assert "400" in case(report, "path_safety").result.error


@respx.mock
async def test_an_empty_map_the_server_refuses_is_caught(real):
    """`{}` is a legitimate request. A server that 4xxs it has not implemented
    the third state at all, which is a different fault from mishandling it."""
    def refuse_empty(request):
        vendor = json.loads(request.content).get("skill_studio") or {}
        if vendor.get("skills") == {}:
            return httpx.Response(400, text="skills must not be empty")
        return FakeAgent()(request)

    respx.post(CHAT_URL).mock(side_effect=refuse_empty)
    respx.get(SKILLS_URL).mock(
        return_value=httpx.Response(200, json={"version": "v1", "skills": {}})
    )
    report = await run_conformance(CHAT_URL, SKILLS_URL)

    assert case(report, "empty_skills").result.ok is False
    assert "400" in case(report, "empty_skills").result.error


@respx.mock
async def test_the_skills_case_talks_to_the_url_it_was_given(real, configure):
    """Never through the seam, unlike every other read in this platform.

    A deployment on fake seams probing the fake ones is right everywhere else.
    Here it would report a canned workspace, a version and a tier about a server
    that was never contacted — the one result this page must not produce.
    """
    with configure(workspace_impl="fake"):
        mount(FakeAgent())
        report = await run_conformance(CHAT_URL, SKILLS_URL)

    assert case(report, "skills").result.detail == "1 skill file"
    assert any(str(c.request.url) == SKILLS_URL for c in respx.calls)


@respx.mock
async def test_rejecting_unknown_fields_is_caught(real):
    """A server that refuses what it does not recognise breaks on the next
    release of this platform, and the failure will look like ours."""
    mount(FakeAgent(rejects_unknown=True))
    report = await run_conformance(CHAT_URL, SKILLS_URL)

    assert case(report, "unknown_keys").result.ok is False


@respx.mock
async def test_no_skills_endpoint_leaves_the_agent_at_the_entry_tier(real):
    mount(FakeAgent())
    report = await run_conformance(CHAT_URL, "")

    assert case(report, "skills").result.ok is None
    assert report.tier == 0
    # Not a failure sentence: this agent works, for evaluation.
    assert "Evaluation will work" in report.summary
    # A version cannot be judged without a listing to read it from.
    assert "version" not in ids(report)


@respx.mock
async def test_a_derived_version_is_reported_without_being_failed(real):
    """Omitting the version is allowed and weaker. Both halves have to be said:
    a red mark would be wrong, and silence would leave a partial staleness check
    looking like a whole one."""
    respx.post(CHAT_URL).mock(side_effect=FakeAgent())
    respx.get(SKILLS_URL).mock(
        return_value=httpx.Response(200, json={"skills": {"a/SKILL.md": "x"}})
    )
    report = await run_conformance(CHAT_URL, SKILLS_URL)

    assert case(report, "version").result.ok is None
    assert "derived" in case(report, "version").result.detail
    assert report.tier == 2


@respx.mock
async def test_every_case_says_why_it_matters(real):
    """A checklist that reports which line failed and not what it means is a
    worse version of running the curl commands by hand."""
    mount(FakeAgent())
    report = await run_conformance(CHAT_URL, SKILLS_URL)

    assert all(c.why.strip() for c in report.cases)
    assert all(c.title.strip() for c in report.cases)


async def test_no_chat_url_is_one_sentence_not_a_stack_trace(configure):
    with configure(agent_impl="real", agent_chat_url=""):
        report = await run_conformance("", "")
    assert ids(report) == ["chat"]
    assert report.tier == 0
