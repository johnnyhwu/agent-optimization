"""Judge and diagnosis: JSON contract handling, the score threshold, §6.7
truncation of the LLM input, and rejection of hallucinated span indices."""
from __future__ import annotations

import pytest

from app.integrations.base import Span, Trace, Verdict
from app.integrations.real import diagnosis as diagnosis_mod
from app.integrations.real import judge as judge_mod
from app.integrations.real.diagnosis import (
    DiagnosisOutput,
    LlmDiagnosisClient,
    SuspectOutput,
    sanitize_suspects,
    truncate_spans,
)
from app.integrations.real.judge import JudgeOutput, LlmJudgeClient
from app.integrations.real.llm import LlmOutputError, _strip_code_fence
from app.integrations.real.prompts import build_diagnosis_messages, build_judge_messages


def _spans(n=3, body="short") -> list[Span]:
    return [
        Span(index=i, tool_name=f"tool{i}", status="success", input=body, output=body)
        for i in range(n)
    ]


# --- Judge -----------------------------------------------------------------

@pytest.fixture
def judge(configure, monkeypatch):
    def _make(payload: dict):
        async def fake_complete_json(model, messages, schema):
            return schema.model_validate(payload)

        monkeypatch.setattr(judge_mod, "complete_json", fake_complete_json)
        return LlmJudgeClient(model="judge-model")

    with configure(judge_model="judge-model", judge_score_threshold=None):
        yield _make


async def test_judge_trusts_model_verdict_by_default(judge):
    client = judge({"verdict": "incorrect", "score": 0.8, "comment": "missing figure"})
    verdict = await client.judge("q", "a", "gt")
    # score is high but the model said incorrect; without a threshold that wins.
    assert verdict.verdict == "incorrect"
    assert verdict.score == 0.8
    assert verdict.comment == "missing figure"


async def test_threshold_overrides_the_model_verdict(judge, configure):
    client = judge({"verdict": "incorrect", "score": 0.8, "comment": "c"})
    with configure(judge_score_threshold=0.5):
        verdict = await client.judge("q", "a", "gt")
    assert verdict.verdict == "correct"


async def test_threshold_can_fail_a_model_pass(judge, configure):
    client = judge({"verdict": "correct", "score": 0.4, "comment": "c"})
    with configure(judge_score_threshold=0.5):
        verdict = await client.judge("q", "a", "gt")
    assert verdict.verdict == "incorrect"


def test_judge_output_normalizes_case_and_whitespace():
    assert JudgeOutput(verdict=" Correct ", score=0.9).verdict == "correct"


def test_judge_output_rejects_unknown_verdict():
    with pytest.raises(ValueError):
        JudgeOutput(verdict="maybe", score=0.5)


def test_judge_output_rejects_out_of_range_score():
    with pytest.raises(ValueError):
        JudgeOutput(verdict="correct", score=1.4)


def test_judge_prompt_includes_all_three_inputs():
    messages = build_judge_messages("QUESTION", "AGENT", "EXPECTED")
    user = messages[-1]["content"]
    assert "QUESTION" in user and "AGENT" in user and "EXPECTED" in user


# --- Diagnosis --------------------------------------------------------------

def test_truncate_spans_cuts_bodies_but_keeps_every_span(configure):
    long_body = "x" * 5000
    with configure(span_body_max_chars=200):
        out = truncate_spans(_spans(4, body=long_body))
    assert len(out) == 4  # §6.7: cut the body, never the span
    assert all(len(s.output) < 5000 for s in out)
    assert all("chars truncated" in s.output for s in out)


def test_truncate_spans_leaves_short_bodies_alone(configure):
    with configure(span_body_max_chars=200):
        out = truncate_spans(_spans(2, body="tiny"))
    assert [s.output for s in out] == ["tiny", "tiny"]


def test_hallucinated_span_index_is_dropped():
    suspects = [
        SuspectOutput(span_index=1, confidence="high", reason="r", evidence="e"),
        SuspectOutput(span_index=99, confidence="high", reason="r", evidence="e"),
    ]
    cleaned = sanitize_suspects(suspects, {0, 1, 2})
    assert [s["span_index"] for s in cleaned] == [1]


def test_unknown_confidence_falls_back_to_medium():
    assert SuspectOutput(span_index=0, confidence="very sure").confidence == "medium"


async def test_diagnose_returns_the_69_shape(configure, monkeypatch):
    payload = {
        "overall_diagnosis": "diverges at span 1",
        "suspects": [
            {"span_index": 1, "confidence": "high", "reason": "r", "evidence": "e"},
            {"span_index": 42, "confidence": "low", "reason": "r", "evidence": "e"},
        ],
        "caveat": "  ",
    }

    async def fake_complete_json(model, messages, schema):
        return schema.model_validate(payload)

    monkeypatch.setattr(diagnosis_mod, "complete_json", fake_complete_json)
    with configure(diagnosis_model="diag-model"):
        client = LlmDiagnosisClient()
        out = await client.diagnose(
            Trace("corr", _spans(3)), "expected process", Verdict("incorrect", 0.2, "c")
        )

    assert out["overall_diagnosis"] == "diverges at span 1"
    assert [s["span_index"] for s in out["suspects"]] == [1]  # 42 dropped
    assert out["caveat"] is None  # blank caveat normalized away


def test_diagnosis_output_tolerates_a_missing_suspects_array():
    out = DiagnosisOutput.model_validate({"overall_diagnosis": "d"})
    assert out.suspects == []
    assert out.caveat is None


def test_diagnosis_prompt_has_the_four_blocks_in_order():
    messages = build_diagnosis_messages(
        _spans(2), "EXPECTED PROCESS", Verdict("incorrect", 0.1, "JUDGE NOTE")
    )
    assert messages[0]["role"] == "system"
    assert "CLUES, not a verdict" in messages[0]["content"]
    user = messages[1]["content"]
    assert user.index("EXPECTED PROCESS") < user.index("Actual trace")
    assert user.index("Actual trace") < user.index("Judge outcome")
    assert "JUDGE NOTE" in user
    assert '"index": 0' in user  # §6.9: index must be given for every span


# --- Shared LLM helpers -----------------------------------------------------

def test_code_fence_is_stripped():
    assert _strip_code_fence('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_plain_json_is_untouched():
    assert _strip_code_fence('{"a": 1}') == '{"a": 1}'


async def test_repair_attempt_then_give_up(monkeypatch):
    from app.integrations.real import llm as llm_mod

    calls = []

    async def bad_complete(model, messages, json_mode):
        calls.append(messages)
        return "not json at all"

    monkeypatch.setattr(llm_mod, "_complete", bad_complete)
    with pytest.raises(LlmOutputError):
        await llm_mod.complete_json("m", [{"role": "user", "content": "x"}], JudgeOutput)
    # One initial attempt plus exactly one repair attempt.
    assert len(calls) == 2


async def test_repair_attempt_can_succeed(monkeypatch):
    from app.integrations.real import llm as llm_mod

    replies = iter(["oops", '{"verdict": "correct", "score": 0.9, "comment": "ok"}'])

    async def flaky_complete(model, messages, json_mode):
        return next(replies)

    monkeypatch.setattr(llm_mod, "_complete", flaky_complete)
    out = await llm_mod.complete_json("m", [{"role": "user", "content": "x"}], JudgeOutput)
    assert out.verdict == "correct"
