"""Prompt construction for the three LLM seams.

Kept in one file so the wording can be tuned without touching client logic — the
judge's grading criteria, the diagnosis tone constraint and the synthesis level
of detail are the things most likely to be iterated on once real data starts
flowing.
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
    spans: list[Span], ground_truth_reasoning: str, judge_verdict: Verdict | None
) -> list[dict]:
    """§6.9: four blocks, fixed order — task framing (system), expected process,
    truncated trace, judge outcome.

    The fourth block stays in place when there is no verdict (the playground
    allows an expected process with no expected answer, §10.4). Saying "nothing
    was graded" is not the same as omitting the block: without it the model is
    left to infer whether the answer was wrong, and it will assume it was.
    """
    if judge_verdict is None:
        judge_block = (
            "No judgement was made: no expected answer was supplied, so nothing "
            "was graded. Do not assume the final answer was wrong — compare the "
            "trace against the expected process above and report where it diverges."
        )
    else:
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


# --- Synthesis (a first draft of an expected process, from a real trace) -----

SYNTHESIS_SYSTEM = """\
You turn an AI agent's execution trace into a short, step-by-step description of \
what the agent did.

The result is a **first draft** a developer will read and correct. Write it as \
the process itself, not as commentary on a recording — no "the agent appears \
to", no hedging, no judgement about whether any of it was right.

Level of detail — this is the part that matters:
- One numbered step per meaningful action: reading a skill, calling a tool, \
producing the answer.
- For a tool call, name the tool, say what it was asked for, and say what came \
back — in a phrase, not a transcript.
- Do NOT reproduce full payloads, row dumps, long SQL, ids or timestamps. If a \
figure is the point of the step, one figure is enough.
- Merge repeated identical calls into one step ("queried invoices for each of \
the three months").
- Finish with a step describing what the final answer presented.
- Aim for 3-8 steps.

Example of the shape and grain:
1. Read the billing skill to get the procedure for invoice questions.
2. Queried the invoices table for ACME over Q2 and got three monthly balances.
3. Summed the balances and presented the total with the period it covers.

Reply with ONLY a JSON object, no prose and no code fences:
{"reasoning_process": "1. ...\\n2. ...\\n3. ..."}"""


def build_synthesis_messages(
    question: str, agent_response: str, spans: list[Span]
) -> list[dict]:
    """Question, the answer produced, and the trace that produced it.

    The answer is included because the last step has to describe what was
    presented, and the final span's output is often a tool result rather than
    the reply the developer actually saw.
    """
    user = (
        f"# Question\n{question}\n\n"
        "# Trace (span bodies may be truncated; every span is present)\n"
        f"{format_spans(spans)}\n\n"
        f"# The answer the agent gave\n{agent_response or '(no answer was recorded)'}"
    )
    return [
        {"role": "system", "content": SYNTHESIS_SYSTEM},
        {"role": "user", "content": user},
    ]
