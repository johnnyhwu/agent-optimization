"""Prompt construction for the two LLM seams.

Kept in one file so the wording can be tuned without touching client logic — the
judge's grading criteria and the diagnosis tone constraint are the two things
most likely to be iterated on once real data starts flowing.
"""
from __future__ import annotations

import json

from app.integrations.base import Span, Verdict

# --- Judge (§6.7: judge is a black-box sub-component) ------------------------

JUDGE_SYSTEM = """\
You grade a domain agent's answer against a known-correct answer.

Judge on substance, not wording: an answer is correct when it conveys the same \
facts, figures and conclusions as the expected answer. Differences in phrasing, \
ordering or extra harmless detail do not make it incorrect. Missing, wrong or \
contradictory facts do.

Reply with ONLY a JSON object, no prose and no code fences:
{"verdict": "correct" | "incorrect", "score": <number 0.0-1.0>, "comment": "<one or two sentences>"}

"score" is your confidence that the answer is correct (1.0 = certainly correct, \
0.0 = certainly wrong) and must agree with "verdict". "comment" states the \
decisive reason — for an incorrect answer, name the specific fact that is wrong \
or missing."""


def build_judge_messages(question: str, response: str, ground_truth: str) -> list[dict]:
    user = (
        f"# Question\n{question}\n\n"
        f"# Expected answer (ground truth)\n{ground_truth}\n\n"
        f"# Agent's answer\n{response}"
    )
    return [
        {"role": "system", "content": JUDGE_SYSTEM},
        {"role": "user", "content": user},
    ]


# --- Diagnosis (§6.9 input contract, four blocks in a fixed order) -----------

DIAGNOSIS_SYSTEM = """\
You help a developer find where an AI agent's execution trace went wrong.

You are offering CLUES, not a verdict. This constraint is not optional:
- Never claim certainty about which step caused the failure.
- Say plainly when you are unsure, and prefer hedged wording ("appears to", \
"may have", "looks like").
- You may flag several suspicious steps. Order them most-suspicious first.
- If you suspect the error does not localise to any single step — it compounds \
across steps — or that it lies outside what the agent's skill controls (a tool \
or the base model), say so in "caveat" instead of forcing a suspect.

Reply with ONLY a JSON object, no prose and no code fences:
{
  "overall_diagnosis": "one or two plain sentences: roughly where this trace starts diverging from the expected process",
  "suspects": [
    {
      "span_index": <integer, the index of a span shown below>,
      "confidence": "high" | "medium" | "low",
      "reason": "why this step looks suspicious and how it diverges from the expected process",
      "evidence": "a short quote from that span's input or output"
    }
  ],
  "caveat": "<optional; omit or null when it does not apply>"
}

"span_index" MUST be one of the indices listed in the trace. Never invent one."""


def format_spans(spans: list[Span]) -> str:
    """Render the span list for the prompt (§6.9 block 3).

    Every span is kept — §6.7 is explicit that the trace is truncated by body,
    never by dropping spans: a root cause often sits early while the evidence
    that makes it visible sits late.
    """
    rendered = []
    for span in spans:
        block = {
            "index": span.index,
            "tool_name": span.tool_name,
            "status": span.status,
            "input": span.input,
            "output": span.output,
        }
        if span.status_message:
            block["status_message"] = span.status_message
        rendered.append(block)
    return json.dumps(rendered, ensure_ascii=False, indent=2)


def build_diagnosis_messages(
    spans: list[Span], ground_truth_reasoning: str, judge_verdict: Verdict
) -> list[dict]:
    """§6.9: four blocks, fixed order — task framing (system), expected process,
    truncated trace, judge outcome."""
    judge_block = f"The judge marked this answer **{judge_verdict.verdict}**."
    if judge_verdict.comment:
        judge_block += f'\nJudge comment: "{judge_verdict.comment}"'

    user = (
        "# Expected reasoning process\n"
        f"{ground_truth_reasoning}\n\n"
        "# Actual trace (span bodies may be truncated; every span is present)\n"
        f"{format_spans(spans)}\n\n"
        "# Judge outcome\n"
        f"{judge_block}"
    )
    return [
        {"role": "system", "content": DIAGNOSIS_SYSTEM},
        {"role": "user", "content": user},
    ]
