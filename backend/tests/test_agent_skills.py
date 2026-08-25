"""The pre-flight probe the "Run eval" dialog makes before it will start a run.

The dialog collapses its connection settings by default, so the ordinary path is
to press Run without ever looking at them — and to discover a typo in the agent's
base URL only after a run row, a set of `question_results` and a batch of agent
calls have been spent on it. This endpoint is what turns that into a red mark on
the dialog before anything has been started.

It is deliberately the *same* call the playground connects with: reaching
`get_workspace` proves the host is there, that it speaks the contract, and hands
over the skill list the coverage check needs. A dedicated health endpoint would
prove less and be one more thing to keep in step.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.config import settings
from app.integrations.base import Workspace
from app.routers import agent as agent_router
from app.services.agent_skills import top_level_skills


# --- What counts as a skill on an agent -------------------------------------

def test_a_skills_directory_is_named_by_its_top_level_folder():
    assert top_level_skills(
        {
            "billing/SKILL.md": "…",
            "billing/references/refunds.md": "…",
            "reporting/SKILL.md": "…",
        }
    ) == ["billing", "reporting"]


def test_a_skill_stored_as_a_single_file_is_still_a_skill():
    # An agent that keeps one file per skill rather than a directory per skill
    # is not a broken agent, and its skills still have names.
    assert top_level_skills({"escalation": "…"}) == ["escalation"]


def test_an_agent_with_no_skills_is_an_empty_list_not_an_error():
    assert top_level_skills({}) == []


def test_the_optimizer_wizard_and_the_probe_agree_on_the_names():
    """One definition, two callers.

    `optimization/skill-check` reports the same list under `available_skills`,
    and it used to compute it inline. Two implementations of "what is a skill
    here" would eventually disagree about a path, and the symptom would be a
    coverage warning on one screen and none on the other for the same agent.
    """
    from app.routers import optimization as opt

    files = {"billing/SKILL.md": "…", "reporting/SKILL.md": "…"}
    assert top_level_skills(files) == sorted(
        {path.split("/", 1)[0] for path in files}
    )
    assert opt.top_level_skills is top_level_skills


# --- The endpoint -----------------------------------------------------------

async def test_the_probe_reports_the_agents_skills():
    out = await agent_router.agent_skills(subject="alice")
    # The fake workspace's three canned skills.
    assert out.skills == ["billing", "escalation", "reporting"]
    assert out.version


async def test_the_probe_answers_for_the_agent_it_was_given():
    """The dialog is asking about the URL in its own field, not the server's.

    Reading the environment here would let the probe go green against the
    deployment's default agent while the run went somewhere else — which looks
    exactly like a check that passed.
    """
    out = await agent_router.agent_skills(
        agent_base_url="http://agent.example:9000", subject="alice"
    )
    assert out.agent_base_url == "http://agent.example:9000"

    # Blank keeps meaning "the server's own", as it does everywhere else in this
    # config.
    fallback = await agent_router.agent_skills(subject="alice")
    assert fallback.agent_base_url == settings.agent_base_url


async def test_an_unreachable_agent_is_a_503_carrying_the_reason(monkeypatch):
    """Never an empty skill list.

    "This agent has no skills" and "the agent server refused us" must not look
    alike: the first is a coverage warning about the eval set, the second is a
    blocked Start button, and the only thing that can tell them apart is what
    the agent server said.
    """

    class Broken:
        async def get_workspace(self):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(
        agent_router, "_workspace_client", lambda *a, **k: Broken()
    )
    with pytest.raises(HTTPException) as caught:
        await agent_router.agent_skills(
            agent_base_url="http://nowhere.invalid", subject="alice"
        )

    assert caught.value.status_code == 503
    assert "connection refused" in caught.value.detail


async def test_the_probe_spends_its_own_timeout_not_the_runs(monkeypatch, configure):
    """A hung agent must not lock the Start button for two minutes.

    The button is disabled until this answers, which is only safe because the
    probe has a budget of its own. `AGENT_TIMEOUT_S` is the budget for answering
    a question — 120 seconds by default — and borrowing it here would turn "we
    are checking" into a dialog that cannot be used. Same reasoning as the
    playground's `BASELINE_TIMEOUT_S`.
    """
    seen = {}

    def spy(config=None, secrets=None, include_workspace=False):
        seen.update(config or {})

        class Stub:
            async def get_workspace(self):
                return Workspace(version="v1", skills={})

        class Seams:
            workspace = Stub()

        return Seams()

    monkeypatch.setattr(agent_router, "build_seams", spy)
    with configure(agent_probe_timeout_s=5.0, agent_timeout_s=120.0):
        await agent_router.agent_skills(
            agent_base_url="http://agent.example:9000", subject="alice"
        )

    assert seen["agent_timeout_s"] == 5.0
