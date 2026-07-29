"""Fake implementations of the four seams (Stage 1 POC).

Every method simulates realistic latency (values from app/fake_config.py) and
returns deterministic-but-plausible data so the UI + data flow can be exercised
end to end without any real HTTP agent / LLM / Langfuse.

Determinism: outcomes are derived from a hash of the question (so a re-run is
stable) but can be forced with markers embedded in the question text, letting the
demo guarantee specific cases:
    ⟦timeout⟧  -> agent "times out"  -> question status=failed  (§7.1 #4)
    ⟦wrong⟧    -> judge returns incorrect
    ⟦caveat⟧   -> diagnosis attaches a caveat (§6.8)

Each class is the thing you replace to go live.
"""
from __future__ import annotations

import asyncio
import hashlib
import random

from app import fake_config as fc
from app.integrations.base import (
    NOT_READY,
    AgentResponse,
    NotReady,
    Span,
    Trace,
    Verdict,
)

# In-process poll counter so fetch_trace returns NotReady for the first
# TRACE_NOT_READY_POLLS calls per correlation_id (simulates async ingestion).
_poll_counts: dict[str, int] = {}

# A correlation id containing this never becomes ready — the seed uses it to keep
# the "trace is generating" UI state reachable.
NOT_READY_MARKER = "notready"


def _rng(seed: str) -> random.Random:
    h = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    return random.Random(h)


async def _sleep_between(lo: float, hi: float) -> None:
    await asyncio.sleep(random.uniform(lo, hi))


def build_fake_trace(correlation_id: str) -> Trace:
    """Deterministic span tree for a correlation_id.

    Shared by the orchestrator (diagnosis input) and the span-detail view so the
    diagnosis's span_index always lines up with what the UI renders. One span is
    given an over-long body to exercise §6.7 truncation at view time.
    """
    rng = _rng(correlation_id)
    n = rng.randint(5, 8)
    tools = ["read_skill", "sql_query", "sql_query", "vector_search", "summarize",
             "sql_query", "format_response", "generate_response"]
    spans: list[Span] = []
    long_span = rng.randint(1, n - 2)  # a middle span gets a huge body
    for i in range(n):
        tool = tools[i % len(tools)]
        out = f"result rows for step {i}: " + ", ".join(f"row{j}" for j in range(4))
        if i == long_span:
            # Over-long body -> truncated (head+tail kept) when served to the UI.
            out = ("BEGIN_LONG_OUTPUT " + "x-data-cell " * 400 + "END_LONG_OUTPUT")
        spans.append(
            Span(
                index=i,
                tool_name=tool,
                status="success",
                input=f"input context for span {i} (tool={tool})",
                output=out,
                token_usage={"input": rng.randint(80, 400),
                             "output": rng.randint(40, 300),
                             "total": rng.randint(120, 700)},
            )
        )
    return Trace(correlation_id=correlation_id, spans=spans)


def _intended_verdict(question: str) -> str:
    if "⟦wrong⟧" in question:
        return "incorrect"
    # ~30% incorrect by hash, otherwise correct.
    return "incorrect" if _rng(question).random() < 0.30 else "correct"


class FakeAgentClient:
    # REPLACE WITH REAL IMPL: POST the agent HTTP server's /execute endpoint,
    # passing correlation_id as metadata.trace_data.trace_id (§6.2) so the
    # agent applies it to its Langfuse trace.
    async def call(
        self, question: str, correlation_id: str, user_id: str,
        tags: list[str] | None = None,
    ) -> AgentResponse:
        await _sleep_between(fc.AGENT_LATENCY_MIN_S, fc.AGENT_LATENCY_MAX_S)
        if "⟦timeout⟧" in question:
            return AgentResponse(
                response="", correlation_id=correlation_id, failed=True,
                error="Simulated agent timeout (⟦timeout⟧ marker).",
            )
        verdict = _intended_verdict(question)
        # Encode intended verdict into the response so the fake judge is consistent.
        body = "Here is the agent's answer based on the retrieved data."
        return AgentResponse(response=f"[[v:{verdict}]] {body}", correlation_id=correlation_id)


class FakeJudgeClient:
    # REPLACE WITH REAL IMPL: run the real LLM-as-judge (§6.7 black box) — question
    # + response + ground_truth in, {verdict, score, comment} out.
    async def judge(self, question: str, response: str, ground_truth: str) -> Verdict:
        await _sleep_between(fc.JUDGE_LATENCY_MIN_S, fc.JUDGE_LATENCY_MAX_S)
        verdict = "correct"
        if "[[v:incorrect]]" in response:
            verdict = "incorrect"
        if verdict == "correct":
            return Verdict(verdict="correct", score=0.92,
                           comment="Answer matches the expected response.")
        return Verdict(
            verdict="incorrect", score=0.34,
            comment="Answer is missing key facts present in the expected response.",
        )


class FakeTraceClient:
    # REPLACE WITH REAL IMPL: GET /api/public/v2/observations?traceId={correlation_id}
    # from Langfuse and rebuild the span tree (§3.1). Return NotReady until
    # ingestion lands (§6.12).
    async def fetch_trace(self, correlation_id: str) -> Trace | NotReady:
        await asyncio.sleep(fc.TRACE_FETCH_LATENCY_S)
        # A correlation id the seed marks as permanently un-ingested, so the
        # "trace is generating" state stays demonstrable now that the view path
        # retries instead of trusting the stored trace_ready flag.
        if NOT_READY_MARKER in correlation_id:
            return NOT_READY
        count = _poll_counts.get(correlation_id, 0)
        _poll_counts[correlation_id] = count + 1
        if count < fc.TRACE_NOT_READY_POLLS:
            return NOT_READY
        return build_fake_trace(correlation_id)


class FakeDiagnosisClient:
    # REPLACE WITH REAL IMPL: build the §6.9 prompt (system tone constraint +
    # ground-truth reasoning + truncated trace + judge verdict) and call the real
    # diagnosis LLM. Must return the §6.9 JSON shape.
    model_name = "fake-diagnosis-v0"

    async def diagnose(self, trace: Trace, ground_truth_reasoning: str,
                       judge_verdict: Verdict) -> dict:
        await _sleep_between(fc.DIAGNOSIS_LATENCY_MIN_S, fc.DIAGNOSIS_LATENCY_MAX_S)
        rng = _rng(trace.correlation_id + "diag")
        spans = trace.spans
        # Pick a primary suspect and, sometimes, a secondary (clue-style, not a
        # verdict — §6.7/§6.9 uncertain tone, multiple suspects allowed).
        primary = rng.randint(1, len(spans) - 1)
        suspects = [{
            "span_index": primary,
            "confidence": "high",
            "reason": (f"Relative to the expected flow, span {primary} "
                       f"({spans[primary].tool_name}) appears to diverge — its result "
                       "looks incomplete for what the next step needs."),
            "evidence": spans[primary].output[:160],
        }]
        if rng.random() < 0.5 and primary - 1 >= 0:
            up = primary - 1
            suspects.append({
                "span_index": up,
                "confidence": "medium",
                "reason": (f"It's also possible the upstream span {up} "
                           f"({spans[up].tool_name}) already dropped data, which would "
                           "only surface downstream."),
                "evidence": spans[up].output[:120],
            })
        caveat = None
        # Caveat is forced by marker (via reasoning text passthrough) or occasional
        # hash — signals "maybe not a single span / not skill-controllable" (§6.8).
        if "⟦caveat⟧" in ground_truth_reasoning or rng.random() < 0.2:
            caveat = ("The error may not localize to a single span — it looks like a "
                      "compounding issue across retrieval and generation, possibly "
                      "outside what the skill controls (tool/base-model).")
        return {
            "overall_diagnosis": (
                f"The trace seems to start diverging around span {primary}; the final "
                "answer likely went wrong because that step's output was thin."),
            "suspects": suspects,
            "caveat": caveat,
        }
