"""Real SynthesisClient: a first-draft expected process, written from a trace.

Why this exists: `questions.ground_truth_reasoning` is NOT NULL, so a question
promoted out of the playground cannot be created without one — and a developer
who just watched the agent answer correctly should not have to retype, from
memory, the process they can see in the span list.

**What it is for, and what it is not.** The output describes what the agent
*did*. Whether that is what it *should* do is the developer's call, which is why
this is only ever offered as a draft they edit, and only on a button they press
(§10.8). Synthesising silently — or for every attempt at once — would quietly
turn "what happened" into "what is expected", and the diagnosis step would then
be comparing future traces against a recording of one past run.

Shares the LLM seam with the judge and the diagnosis rather than adding a fourth
external dependency: same client, same failure handling, one more model name.
"""
from __future__ import annotations

from pydantic import BaseModel

from app.config import settings
from app.integrations.base import Trace
from app.integrations.real.diagnosis import truncate_spans
from app.integrations.real.llm import complete_json
from app.integrations.real.prompts import build_synthesis_messages


class SynthesisOutput(BaseModel):
    reasoning_process: str


class LlmSynthesisClient:
    def __init__(self, model: str | None = None, llm=None) -> None:
        self.model_name = model or settings.synthesis_model
        if not self.model_name:
            raise RuntimeError(
                "SYNTHESIS_IMPL=real but no synthesis model was given — set it "
                "in the playground config, or via SYNTHESIS_MODEL."
            )
        # None = the environment-configured endpoint.
        self.llm = llm

    async def synthesize(self, trace: Trace, question: str, agent_response: str) -> str:
        # Same §6.7 truncation the diagnosis path uses: cut the body, never the
        # span. A step that only makes sense because of an earlier one is the
        # normal case, so dropping spans would produce a plausible-looking
        # process with a hole in it.
        spans = truncate_spans(trace.spans)
        messages = build_synthesis_messages(question, agent_response, spans)
        out = await complete_json(self.model_name, messages, SynthesisOutput, client=self.llm)
        return out.reasoning_process.strip()
