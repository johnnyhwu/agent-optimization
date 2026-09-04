"""The two pre-flight probes every screen that names an agent shares.

The Run-eval dialog collapses its connection settings by default, so the
ordinary path is to press Run without ever looking at them — and to discover a
typo only after a run row, a set of `question_results` and a batch of agent calls
have been spent on it. These endpoints turn that into a mark on the dialog before
anything has been started.

They are split by cost. Reading the skills endpoint is a free GET, so callers
fire it while the developer is typing; the chat probe spends a real model call,
so nothing triggers it on its own.

The tri-state in `check.ok` is what the rest of the platform is built on:
`true` for a listing, `false` for a failure carrying the agent's own words, and
`null` for "no skills endpoint was configured" — which is a supported way to run
an agent, not a fault to report.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.config import settings
from app.integrations.base import Workspace
from app.routers import agent as agent_router
from app.schemas import ChatProbeIn, RunConfig
from app.services import agent_probe
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
    assert out.check.ok is True


async def test_the_probe_answers_for_the_agent_it_was_given():
    """The dialog is asking about the URL in its own field, not the server's.

    Reading the environment here would let the probe go green against the
    deployment's default agent while the run went somewhere else — which looks
    exactly like a check that passed.
    """
    out = await agent_router.agent_skills(
        agent_skills_url="http://agent.example:9000/skills", subject="alice"
    )
    assert out.agent_skills_url == "http://agent.example:9000/skills"

    # Blank keeps meaning "the server's own", as it does everywhere else in this
    # config.
    fallback = await agent_router.agent_skills(subject="alice")
    assert fallback.agent_skills_url == settings.agent_skills_url


async def test_an_unreachable_agent_is_a_failed_check_not_an_empty_list(monkeypatch):
    """Never an empty skill list, and never a 5xx either.

    "This agent has no skills" and "the agent server refused us" must not look
    alike: the first is a coverage warning about the eval set, the second is a
    connection to fix, and the only thing that can tell them apart is what the
    agent server said. It comes back inside a 200 because the caller has three
    outcomes to draw, not two — see `check.ok is None` below.
    """

    class Broken:
        async def get_workspace(self):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(
        agent_probe, "build_seams",
        lambda *a, **k: SimpleNamespace(workspace=Broken()),
    )
    out = await agent_router.agent_skills(
        agent_skills_url="http://nowhere.invalid/skills", subject="alice"
    )

    assert out.check.ok is False
    assert "connection refused" in out.check.error
    assert out.skills == []


async def test_no_skills_endpoint_is_neither_success_nor_failure(configure, monkeypatch):
    """The entry-level tier, and the reason this endpoint stopped raising.

    An agent with only a chat endpoint is evaluated normally. Reporting that as
    an error blocked every such agent from a feature that never needed the
    skills listing in the first place.
    """
    monkeypatch.setattr(
        agent_probe, "build_seams",
        lambda *a, **k: SimpleNamespace(workspace=None),
    )
    out = await agent_router.agent_skills(agent_skills_url="", subject="alice")

    assert out.check.ok is None
    assert out.check.error == ""
    assert out.skills == []


async def test_the_probe_spends_its_own_timeout_not_the_runs(monkeypatch, configure):
    """A hung agent must not lock the Start button for two minutes.

    The button is disabled until this answers, which is only safe because the
    probe has a budget of its own. `AGENT_TIMEOUT_S` is the budget for answering
    a question — 120 seconds by default — and borrowing it here would turn "we
    are checking" into a dialog that cannot be used. Same reasoning as the
    playground's `BASELINE_TIMEOUT_S`.
    """
    seen = {}

    def spy(config=None, secrets=None, include_workspace=False, **kwargs):
        seen.update(config or {})

        class Stub:
            async def get_workspace(self):
                return Workspace(version="v1", skills={})

        return SimpleNamespace(workspace=Stub())

    monkeypatch.setattr(agent_probe, "build_seams", spy)
    with configure(agent_probe_timeout_s=5.0, agent_timeout_s=120.0):
        await agent_router.agent_skills(
            agent_skills_url="http://agent.example:9000/skills", subject="alice"
        )

    assert seen["agent_timeout_s"] == 5.0


# --- The chat probe ---------------------------------------------------------

def _probe_body(**kwargs):
    return ChatProbeIn(
        config=RunConfig(agent_chat_url="http://agent.example:9000/v1/chat/completions"),
        **kwargs,
    )


async def test_the_chat_probe_reports_a_working_agent(monkeypatch):
    async def fake_probe(chat_url, timeout_s, *, with_override, trace_client):
        assert chat_url == "http://agent.example:9000/v1/chat/completions"
        return agent_probe.ChatProbeResult(
            chat=agent_probe.CheckResult(ok=True, detail="answered in 1.2s"),
            override=agent_probe.CheckResult(ok=True, detail="override applied"),
            trace_id="t1",
        )

    monkeypatch.setattr(agent_router, "probe_chat", fake_probe)
    out = await agent_router.chat_probe(_probe_body(), subject="alice")

    assert out.chat.ok is True
    assert out.override.ok is True
    # Not attempted, and it must not be drawn as a failure: only the wizard asks
    # for it, and only the wizard blocks on it.
    assert out.trace.ok is None


async def test_an_answer_without_the_magic_value_fails_only_the_override(monkeypatch):
    """The two layers of one call have to stay separable.

    An agent that answers is a working chat endpoint even when the override did
    not land — the playground still lets you ask it questions, and only the
    wizard refuses. Collapsing them would take away both.
    """

    async def fake_probe(chat_url, timeout_s, *, with_override, trace_client):
        return agent_probe.ChatProbeResult(
            chat=agent_probe.CheckResult(ok=True, detail="answered in 1.2s"),
            override=agent_probe.CheckResult(ok=False, error="did not contain the value"),
        )

    monkeypatch.setattr(agent_router, "probe_chat", fake_probe)
    out = await agent_router.chat_probe(_probe_body(), subject="alice")

    assert out.chat.ok is True
    assert out.override.ok is False


async def test_the_chat_probe_never_answers_with_a_5xx(monkeypatch):
    """A 500 here reads as a bug in this platform.

    The entire point of the screen calling this is to say something about the
    *agent*, so even a seam that cannot be constructed comes back as a failed
    check with the sentence in it.
    """

    async def boom(*a, **k):
        raise RuntimeError("no agent chat endpoint was given")

    monkeypatch.setattr(agent_router, "probe_chat", boom)
    out = await agent_router.chat_probe(ChatProbeIn(), subject="alice")

    assert out.chat.ok is False
    assert "no agent chat endpoint" in out.chat.error
