"""Real JudgeClient: LLM-as-judge over an OpenAI-compatible endpoint (§6.7).

§6.7 left two things open: the prompt and how a continuous score becomes a
binary verdict. Both are settled here — the model returns verdict and score
together, and `JUDGE_SCORE_THRESHOLD` optionally overrides the verdict from the
score so the boundary can be retuned without touching the prompt.
"""
from __future__ import annotations

from openai import AsyncOpenAI
from pydantic import BaseModel, Field, field_validator

from app.config import settings
from app.integrations.base import Verdict
from app.integrations.real.llm import complete_json
from app.integrations.real.prompts import build_judge_messages


class JudgeOutput(BaseModel):
    """The JSON contract the judge prompt asks for."""

    verdict: str
    score: float = Field(ge=0.0, le=1.0)
    comment: str | None = None

    @field_validator("verdict")
    @classmethod
    def _normalize(cls, value: str) -> str:
        v = value.strip().lower()
        if v not in ("correct", "incorrect"):
            raise ValueError("verdict must be 'correct' or 'incorrect'")
        return v


class LlmJudgeClient:
    def __init__(
        self,
        model: str | None = None,
        llm: AsyncOpenAI | None = None,
        system_prompt: str | None = None,
        user_template: str | None = None,
    ) -> None:
        self.model_name = model or settings.judge_model
        if not self.model_name:
            raise RuntimeError(
                "JUDGE_IMPL=real but no judge model was given — set it in the run "
                "config, or via JUDGE_MODEL."
            )
        # None = the environment-configured endpoint.
        self.llm = llm
        # None on either = this eval set never overrode it; the default in
        # `prompts.py` applies (services/judge_prompt explains the two directions).
        self.system_prompt = system_prompt
        self.user_template = user_template

    async def judge(self, question: str, response: str, ground_truth: str) -> Verdict:
        messages = build_judge_messages(
            question, response, ground_truth,
            system_prompt=self.system_prompt, user_template=self.user_template,
        )
        out = await complete_json(self.model_name, messages, JudgeOutput, client=self.llm)

        verdict = out.verdict
        threshold = settings.judge_score_threshold
        if threshold is not None:
            # Score-derived verdict wins, so the pass/fail boundary is a config
            # change rather than a prompt change.
            verdict = "correct" if out.score >= threshold else "incorrect"

        return Verdict(verdict=verdict, score=out.score, comment=out.comment)
