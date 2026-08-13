"""Real DiagnosisClient: coarse-grained trace error localization (§6.9).

Two things here are load-bearing beyond "call an LLM":

1. **The trace fed to the model is truncated** with the same §6.7 rule the view
   path uses — cut the body, never the span. Until now truncation only happened
   at render time, so a real trace would have gone to the model at full length.

2. **`span_index` is validated against the spans we actually sent.** The UI jumps
   straight to `suspects[0].span_index`, so a hallucinated index would land the
   developer on a span that does not exist. Out-of-range suspects are dropped.
"""
from __future__ import annotations

import logging

from openai import AsyncOpenAI
from pydantic import BaseModel, Field, field_validator

from app.config import settings
from app.integrations.base import Span, Trace, Verdict
from app.integrations.real.llm import complete_json
from app.integrations.real.prompts import build_diagnosis_messages
from app.services.truncation import truncate_trace

log = logging.getLogger(__name__)

_CONFIDENCES = ("high", "medium", "low")


class SuspectOutput(BaseModel):
    span_index: int
    confidence: str = "medium"
    reason: str = ""
    evidence: str = ""

    @field_validator("confidence")
    @classmethod
    def _normalize(cls, value: str) -> str:
        v = (value or "").strip().lower()
        return v if v in _CONFIDENCES else "medium"


class DiagnosisOutput(BaseModel):
    """The §6.9 output contract."""

    overall_diagnosis: str
    suspects: list[SuspectOutput] = Field(default_factory=list)
    caveat: str | None = None


def truncate_spans(spans: list[Span]) -> list[Span]:
    """§6.7 applied to the LLM input: every span kept, long bodies shortened.

    One cascade, shared with the reflect stage. This used to cap each body
    independently, which was simple and wasteful: a trace with one 50 KB tool
    result and forty short spans had the big one cut to the same limit as the
    rest, while the rest left almost all of their allowance unspent — so the
    prompt lost the evidence and kept the padding.

    The budget is the same total that per-body capping allowed at worst
    (`span_body_max_chars` for each side of each span), and `min_keep` is that
    same number, so a trace where *everything* is oversized comes out exactly as
    it did before. What changes is every other trace: unused allowance is handed
    back and the big bodies keep more of themselves. It also inherits the rest of
    the cascade — tool-call arguments are never cut, tool results go before
    conversation, and a trace that already fits is not touched at all.
    """
    limit = settings.span_body_max_chars
    trimmed, _ = truncate_trace(
        Trace(correlation_id="", spans=list(spans)),
        budget_chars=limit * 2 * max(len(spans), 1),
        min_keep=limit,
        # Diagnosis has no "drop an item" move the way reflection does: there is
        # one trace and it has to fit. A cut final answer beats a request that
        # overflows the context window.
        cut_final_answer=True,
    )
    return list(trimmed.spans)


def sanitize_suspects(suspects: list[SuspectOutput], valid_indices: set[int]) -> list[dict]:
    """Drop suspects pointing at spans that don't exist."""
    cleaned = []
    for suspect in suspects:
        if suspect.span_index not in valid_indices:
            log.warning(
                "diagnosis returned span_index=%s, which is not in the trace; dropping",
                suspect.span_index,
            )
            continue
        cleaned.append(suspect.model_dump())
    return cleaned


class LlmDiagnosisClient:
    def __init__(self, model: str | None = None, llm: AsyncOpenAI | None = None) -> None:
        self.model_name = model or settings.diagnosis_model
        if not self.model_name:
            raise RuntimeError(
                "DIAGNOSIS_IMPL=real but no diagnosis model was given — set it in "
                "the run config, or via DIAGNOSIS_MODEL."
            )
        # None = the environment-configured endpoint.
        self.llm = llm

    async def diagnose(
        self, trace: Trace, ground_truth_reasoning: str,
        judge_verdict: Verdict | None,
    ) -> dict:
        spans = truncate_spans(trace.spans)
        messages = build_diagnosis_messages(spans, ground_truth_reasoning, judge_verdict)
        out = await complete_json(self.model_name, messages, DiagnosisOutput, client=self.llm)

        suspects = sanitize_suspects(out.suspects, {s.index for s in spans})
        caveat = (out.caveat or "").strip() or None

        return {
            "overall_diagnosis": out.overall_diagnosis,
            "suspects": suspects,
            "caveat": caveat,
        }
