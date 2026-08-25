"""Did the agent actually use the skill we sent it, or its own copy?

This is a different question from the one `detect_activation` answers, and until
now nothing asked it. Both detectors prove the *skill* was loaded: the tool-path
one matches a directory name in a tool call, the content one matches lines of the
skill's own body. Neither can tell our candidate from the agent's deployed copy —
and a candidate is usually a small edit of that copy, so the longest lines (the
ones the content detector picks as markers) are typically identical in both.

The consequence, before this: an agent server that ignored `metadata.skills`
would run every step against its own unchanging files. Activation would read
100%, the pre-flight would pass, accuracy would sit flat, and nothing anywhere
would say why. An hour and a few hundred agent calls to learn nothing.

The fix is to make the pre-flight's copy *distinguishable*: one marker line that
exists only in what we sent, plus an instruction telling the agent to read the
skill so the file's contents land in the trace whether it injects skills into the
prompt or reads them with a tool. Then the marker's absence means something.

Three outcomes, and the third one matters as much as the others:

  * marker seen               -> the override works
  * marker absent, skill read -> the agent used its own copy; stop the run
  * nothing observed          -> unknown; warn, never block

Only the pre-flight question carries any of this. The scored rollouts are sent
exactly as they were, because the whole point of the run is measuring the effect
of the candidate text and nothing else.
"""
from __future__ import annotations

import uuid

import pytest

from app.integrations.base import Span, Trace
from app.optimizer import adapter, engine
from app.optimizer.skillio import frontmatter_span
from app.optimizer.store import Item, ResultRow, RunSpec

from tests.test_optimizer_engine import (
    RecordingStore,
    Scores,
    install_update,
    make_items,
    make_spec,
    run,
)

FRONTMATTER_SKILL = {
    "billing/SKILL.md": (
        "---\n"
        "name: billing\n"
        "description: Invoices, balances and refunds.\n"
        "---\n"
        "# Billing skill\n"
        "1. Identify the customer.\n"
    ),
    "billing/references/refunds.md": "# Refunds\nProrated by service days.\n",
}

PLAIN_SKILL = {"billing/SKILL.md": "# Billing skill\n1. Identify the customer.\n"}


# --- The marker itself ------------------------------------------------------


def test_a_marker_is_unique_per_probe():
    """Two runs must not be able to validate each other's traces."""
    assert adapter.make_probe_marker() != adapter.make_probe_marker()


def test_the_marker_is_injected_after_the_frontmatter():
    """Frontmatter has to stay first: routing mode optimises the description in
    it, and `frontmatter_span` finds it by the leading `---`."""
    marker = "probe-abc123"
    out = adapter.inject_probe_marker(FRONTMATTER_SKILL, "billing", marker)
    lines = out["billing/SKILL.md"].splitlines()

    assert lines[0] == "---"
    assert lines[3] == "---"
    # First line of the body, not the last line of the file: an agent's read
    # tool may cap how much of a long file it returns, and the end goes first.
    assert marker in lines[4]


def test_the_marker_goes_first_when_there_is_no_frontmatter():
    out = adapter.inject_probe_marker(PLAIN_SKILL, "billing", "probe-abc123")
    assert "probe-abc123" in out["billing/SKILL.md"].splitlines()[0]


def test_the_marker_is_a_comment_so_it_reads_as_noise_not_instruction():
    out = adapter.inject_probe_marker(PLAIN_SKILL, "billing", "probe-abc123")
    line = out["billing/SKILL.md"].splitlines()[0]
    assert line.startswith("<!--") and line.endswith("-->")


def test_injection_leaves_the_original_untouched():
    """The caller keeps holding the real candidate; only the copy is marked."""
    before = dict(FRONTMATTER_SKILL)
    adapter.inject_probe_marker(FRONTMATTER_SKILL, "billing", "probe-abc123")
    assert FRONTMATTER_SKILL == before


def test_injection_does_not_touch_the_other_files():
    out = adapter.inject_probe_marker(FRONTMATTER_SKILL, "billing", "probe-abc123")
    assert out["billing/references/refunds.md"] == (
        FRONTMATTER_SKILL["billing/references/refunds.md"]
    )


def test_the_marker_never_lands_above_the_frontmatter_on_a_crlf_file():
    """A `\r\n` SKILL.md is ordinary on Windows, and `frontmatter_span` does not
    parse one. Falling back to "insert at offset 0" put the comment *above* the
    opening `---`, which stops the block being frontmatter at all — in routing
    mode that is the very text being optimised."""
    text = "---\r\nname: billing\r\ndescription: d\r\n---\r\n# Billing\r\n"
    out = adapter.inject_probe_marker({"billing/SKILL.md": text}, "billing", "probe-abc")

    body = out["billing/SKILL.md"]
    assert body.startswith("---")
    # Still a parseable frontmatter block after the edit.
    assert frontmatter_span(body.replace("\r\n", "\n")) is not None
    assert "probe-abc" in body


def test_the_marker_never_glues_itself_to_the_closing_delimiter():
    """An unterminated frontmatter (no trailing newline) reported a span ending
    at EOF, so the comment was appended straight onto the `---`."""
    text = "---\nname: billing\n---"
    out = adapter.inject_probe_marker({"billing/SKILL.md": text}, "billing", "probe-abc")

    lines = out["billing/SKILL.md"].splitlines()
    assert "---<!--" not in out["billing/SKILL.md"]
    # The closing delimiter survives as a line of its own.
    assert lines[2] == "---"
    assert "probe-abc" in lines[3]


def test_the_marker_always_occupies_a_line_of_its_own():
    for text in (
        "---\nname: b\n---\n# Body\n",
        "---\r\nname: b\r\n---\r\n# Body\r\n",
        "---\nname: b\n---",
        "# Body only\n",
        "",
    ):
        out = adapter.inject_probe_marker({"b/SKILL.md": text}, "b", "probe-abc")
        marker_lines = [
            ln for ln in out["b/SKILL.md"].replace("\r\n", "\n").split("\n")
            if "probe-abc" in ln
        ]
        assert len(marker_lines) == 1, text
        assert marker_lines[0].strip().startswith("<!--"), text
        assert marker_lines[0].strip().endswith("-->"), text


def test_a_skill_with_no_entry_point_is_returned_unchanged():
    """Nowhere to put the marker is a reason to learn nothing, not to crash."""
    files = {"billing/references/only.md": "text"}
    assert adapter.inject_probe_marker(files, "billing", "probe-abc123") == files


# --- The instruction --------------------------------------------------------


def test_the_probe_question_asks_the_agent_to_read_the_skill():
    asked = engine.probe_question("What is the balance?", "billing")
    assert asked.startswith("What is the balance?")
    assert "billing" in asked
    assert "read" in asked.lower()


def test_the_probe_question_names_the_directory_not_a_file_path():
    """A path in the prompt is a path the agent can echo into a tool call, which
    would make the tool-path detector fire on our own instruction."""
    asked = engine.probe_question("q", "billing")
    assert "SKILL.md" not in asked
    assert "/" not in asked.replace("\n", "")


# --- The verdict, at the rollout layer --------------------------------------


def _trace(text: str) -> Trace:
    return Trace(
        correlation_id="c",
        spans=[Span(index=0, tool_name="generation", status="success",
                    input=text, output="an answer")],
    )


def test_a_marker_in_the_payload_verifies_the_override():
    assert adapter.verify_probe_marker(_trace("...probe-abc123..."), "probe-abc123") is True


def test_a_marker_absent_from_a_trace_that_shows_the_skill_is_a_negative():
    assert adapter.verify_probe_marker(
        _trace("nothing here"), "probe-abc123", content_visible=True
    ) is False


def test_no_trace_is_unknown_rather_than_a_negative():
    """Langfuse ingestion fails sometimes; that is not the agent's fault and
    must not be read as it ignoring us."""
    assert adapter.verify_probe_marker(None, "probe-abc123") is None
    assert adapter.verify_probe_marker(Trace("c", []), "probe-abc123") is None


def test_no_marker_asked_for_is_unknown():
    assert adapter.verify_probe_marker(_trace("anything"), None) is None


def test_a_missing_marker_is_only_a_negative_when_file_content_is_visible():
    """The trap this closes: a tool-using agent whose trace carries the tool
    *call* but not its *result*.

    `detect_activation` fires `tool_path` on a path found in a tool call's
    arguments — which proves the agent went to read the skill, and proves
    nothing at all about whether the file's text was ever logged. If the
    marker's absence were taken as evidence there, an agent that applies the
    override perfectly but logs its tool results elsewhere would have every
    optimization run hard-failed with a false accusation.

    So the negative needs positive proof that this trace shows file content at
    all — which the body detector already establishes, and which holds whether
    the agent used our copy or its own, since the pre-flight sends the agent's
    own files.
    """
    seen = _trace("nothing recognisable here")
    assert adapter.verify_probe_marker(seen, "probe-abc123", content_visible=False) is None
    assert adapter.verify_probe_marker(seen, "probe-abc123", content_visible=True) is False


def test_a_marker_that_is_present_is_a_positive_regardless(): 
    """Seeing it is proof on its own — nothing else has to corroborate."""
    seen = _trace("...probe-abc123...")
    assert adapter.verify_probe_marker(seen, "probe-abc123", content_visible=False) is True


# --- What actually reaches the agent ----------------------------------------


@pytest.fixture(autouse=True)
def _no_trace_polling(configure):
    """These tests script the trace directly, so waiting for one to land is
    dead time — a `None` trace would otherwise be re-polled with backoff."""
    with configure(trace_poll_max_attempts=1, trace_poll_backoff_s=[0.0]):
        yield


class _RecordingSeams:
    """Enough of a seam set for `run_rollout`, capturing what the agent was sent."""

    def __init__(self, trace: Trace | None = None):
        self.sent: list = []
        self._trace = trace

        outer = self

        class _Agent:
            async def call(self, question, correlation_id, user_id, tags, workspace=None):
                from app.integrations.base import AgentResponse
                outer.sent.append({"question": question, "workspace": workspace})
                return AgentResponse(
                    response="an answer", correlation_id=correlation_id, latency_ms=1
                )

        class _Judge:
            async def judge(self, question, response, ground_truth):
                from app.integrations.base import Verdict
                return Verdict(verdict="correct", score=1.0, comment="")

        class _Trace:
            async def fetch_trace(self, correlation_id):
                return outer._trace

        self.agent, self.judge, self.trace = _Agent(), _Judge(), _Trace()


async def test_the_marker_reaches_the_agent_in_the_skills_it_is_sent():
    """End to end through the real rollout: the marked copy is what travels."""
    seams = _RecordingSeams()
    await adapter.run_rollout(
        [Item(item_key="k", question="q", ground_truth_response="gt",
              ground_truth_reasoning="r")],
        skill_files=dict(PLAIN_SKILL), mode="isolated", skill_name="billing",
        seams=seams, config={}, probe_marker="probe-abc123",
    )

    override = seams.sent[0]["workspace"]
    assert "probe-abc123" in override.skills["billing/SKILL.md"]


async def test_without_a_marker_the_agent_gets_the_candidate_verbatim():
    seams = _RecordingSeams()
    await adapter.run_rollout(
        [Item(item_key="k", question="q", ground_truth_response="gt",
              ground_truth_reasoning="r")],
        skill_files=dict(PLAIN_SKILL), mode="isolated", skill_name="billing",
        seams=seams, config={},
    )

    assert seams.sent[0]["workspace"].skills == PLAIN_SKILL


async def test_the_marker_does_not_inflate_activation():
    """`detect_activation` must run against the unmarked candidate.

    If it saw the marked copy, the marker line could be picked as one of the
    body markers — and it is guaranteed to be in the payload, because we put it
    there. Activation would read 100% by construction.
    """
    seams = _RecordingSeams(trace=_trace("probe-abc123 and nothing else"))
    rows = await adapter.run_rollout(
        [Item(item_key="k", question="q", ground_truth_response="gt",
              ground_truth_reasoning="r")],
        skill_files=dict(PLAIN_SKILL), mode="isolated", skill_name="billing",
        seams=seams, config={}, probe_marker="probe-abc123", detectable=True,
    )

    assert rows[0].override_verified is True
    # The trace carries the marker and nothing from the skill body, so the
    # detector has seen no evidence the skill itself was loaded.
    assert rows[0].activated is False


# --- The verdict, through the engine ----------------------------------------


def probe_returning(*, verified, hit="tool_path", activated=True):
    """A pre-flight whose single row carries a scripted override verdict."""

    async def fake_probe(*args, **kwargs):
        row = ResultRow(item_key="probe", correlation_id="p", status="done")
        row.activated = activated
        row.detector_hit = hit
        row.skills_read = ["billing"] if activated else []
        row.override_verified = verified
        return [row]

    return fake_probe


def preflight_event(events):
    return next(e for e in events if e["type"] == "preflight")


async def engine_run(monkeypatch, probe, *, mode="isolated"):
    store = RecordingStore(
        make_spec(mode=mode, initial_skill=dict(PLAIN_SKILL)),
        make_items(4), make_items(2, "val"),
    )
    Scores({}).install(monkeypatch, store)
    install_update(monkeypatch)
    monkeypatch.setattr(engine, "probe_activation", probe)
    return store, *await run(store, monkeypatch)


async def test_a_verified_override_lets_the_run_proceed(monkeypatch):
    store, status, events = await engine_run(monkeypatch, probe_returning(verified=True))

    assert status == "completed"
    assert preflight_event(events)["override_verified"] is True


@pytest.mark.parametrize("mode", ["isolated", "routing"])
async def test_an_ignored_override_stops_the_run_in_both_modes(monkeypatch, mode):
    """Neither mode can measure anything once the agent is answering from its
    own files: isolated's accuracy and routing's activation are both readings of
    a skill nobody sent."""
    store, status, events = await engine_run(
        monkeypatch, probe_returning(verified=False), mode=mode
    )

    assert status == "failed"
    event = preflight_event(events)
    assert event["override_verified"] is False
    assert "metadata.skills" in event["message"]
    # Stopped before spending a batch, which is the entire point of doing this
    # in the pre-flight.
    assert store.steps == []


async def test_a_tool_using_agent_that_logs_no_file_content_is_not_accused(monkeypatch):
    """End of the same story, at the engine: `override_verified` arrives as
    `None` and the run proceeds, rather than being stopped on a guess."""
    store, status, events = await engine_run(
        monkeypatch, probe_returning(verified=None, hit="tool_path")
    )

    assert status == "completed"
    assert preflight_event(events)["override_verified"] is None


async def test_a_negative_with_nothing_observed_still_does_not_block(monkeypatch):
    """The engine's second guard, on a combination the adapter cannot produce.

    `verify_probe_marker` only answers `False` once body text has been seen, and
    the detector calls that `content` — so a `False` alongside `hit == "none"`
    is already impossible upstream. The guard is kept because this branch
    hard-fails a run somebody is paying for, and this test is what stops it
    being tidied away as dead code.
    """
    store, status, events = await engine_run(
        monkeypatch, probe_returning(verified=False, hit="none", activated=False)
    )

    assert status == "completed"
    assert store.steps, "the run should have gone on to do work"


async def test_a_probe_that_reports_no_verdict_does_not_block(monkeypatch):
    """`None` is the default on every `ResultRow`, so anything that did not
    actually run the marker check reads as "unknown" and lets the run go on."""
    store, status, events = await engine_run(monkeypatch, probe_returning(verified=None))

    assert status == "completed"
    assert preflight_event(events)["override_verified"] is None


async def test_the_verdict_is_persisted_with_the_rest_of_the_pre_flight(monkeypatch):
    """A resumed run does not re-probe, so the answer has to survive on the row."""
    store, _, _ = await engine_run(monkeypatch, probe_returning(verified=True))

    detector = next(
        u["detector"] for u in reversed(store.run_updates) if "detector" in u
    )
    assert detector["preflight"]["override_verified"] is True


# --- What the scored rollouts are sent --------------------------------------


async def test_only_the_probe_carries_the_marker_and_the_instruction(monkeypatch):
    """The experiment measures the candidate text. A marker line or an extra
    sentence in the scored questions would be a second variable in every number
    the chart draws.
    """
    sent: list[dict] = []

    async def recording_rollout(items, *, skill_files, mode, skill_name, seams,
                                config, **kwargs):
        sent.append({
            "questions": [i.question for i in items],
            "skill_files": dict(skill_files),
            "probe_marker": kwargs.get("probe_marker"),
        })
        from tests.test_optimizer_engine import make_rows
        return make_rows(len(items), correct=len(items))

    store = RecordingStore(
        make_spec(initial_skill=dict(PLAIN_SKILL)), make_items(4), make_items(2, "val")
    )
    monkeypatch.setattr(engine, "run_rollout", recording_rollout)
    install_update(monkeypatch)
    await run(store, monkeypatch)

    # The injection itself happens inside `run_rollout`, which is stubbed here
    # and unit-tested above; what this test pins is *which* calls ask for it.
    probe, scored = sent[0], sent[1:]
    assert probe["probe_marker"]
    assert "read the billing skill" in probe["questions"][0]

    assert scored, "the run should have gone on to score something"
    for call in scored:
        assert call["probe_marker"] is None
        for text in call["skill_files"].values():
            assert "probe-" not in text
        for question in call["questions"]:
            assert "read the billing skill" not in question
