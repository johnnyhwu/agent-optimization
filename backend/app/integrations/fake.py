"""Fake implementations of the six seams (Stage 1 POC + §10 playground).

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
import json
import random
import time

from app import fake_config as fc
from app.integrations.base import (
    NOT_READY,
    AgentResponse,
    NotReady,
    Span,
    Trace,
    Verdict,
    Workspace,
    WorkspaceOverride,
)

# In-process poll counter so fetch_trace returns NotReady for the first
# TRACE_NOT_READY_POLLS calls per correlation_id (simulates async ingestion).
_poll_counts: dict[str, int] = {}

# Workspace overrides the fake agent was asked to use, keyed by correlation_id,
# so the fake trace built later can show them in its system prompt (§10.7). The
# real path has no equivalent bookkeeping: there the injected text shows up in
# the trace because the agent genuinely used it, which is the only evidence the
# platform can ever offer that an override took effect.
_workspace_overrides: dict[str, WorkspaceOverride] = {}

# A correlation id containing this never becomes ready — the seed uses it to keep
# the "trace is generating" UI state reachable.
NOT_READY_MARKER = "notready"


def _rng(seed: str) -> random.Random:
    h = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    return random.Random(h)


async def _sleep_between(lo: float, hi: float) -> None:
    await asyncio.sleep(random.uniform(lo, hi))


# The tool catalogue every fake generation is offered, shaped like the OpenAI
# `tools` array a real agent sends — the span view renders it as such.
_FAKE_TOOL_SPECS = {
    "read_skill": ("Load the developer-written playbook for a class of question.",
                   {"skill": {"type": "string", "description": "skill name"}}),
    "sql_query": ("Run a read-only SQL query against the warehouse.",
                  {"sql": {"type": "string", "description": "a SELECT statement"}}),
    "vector_search": ("Semantic search over the knowledge base.",
                      {"query": {"type": "string"}, "top_k": {"type": "integer"}}),
    "summarize": ("Condense intermediate results into a short brief.",
                  {"text": {"type": "string"}}),
    "format_response": ("Render the final answer in the customer-facing format.",
                        {"answer": {"type": "string"}}),
    "generate_response": ("Produce the final natural-language answer.",
                          {"answer": {"type": "string"}}),
}


def _dump(value: object) -> str:
    """Text rendering of a span body, matching what the real client's `as_text`
    produces — this is the form the diagnosis prompt is built from."""
    return json.dumps(value, ensure_ascii=False, indent=2)


def _fake_tool_defs() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": list(properties)[:1],
                },
            },
        }
        for name, (description, properties) in _FAKE_TOOL_SPECS.items()
    ]


_FAKE_SYSTEM_PROMPT = (
    "You are a domain support agent. Pick the skill that matches the question, "
    "follow its playbook step by step, and call tools rather than guessing. "
    "Never invent figures: every number in the final answer must come from a "
    "tool result. When the playbook and the data disagree, say so explicitly "
    "instead of silently choosing one."
)


def build_fake_trace(correlation_id: str) -> Trace:
    """Deterministic span tree for a correlation_id.

    Shared by the orchestrator (diagnosis input) and the span-detail view so the
    diagnosis's span_index always lines up with what the UI renders.

    Each span is shaped like a real LLM generation — input is the
    `{"tools": [...], "messages": [...]}` request with the conversation
    accumulated so far, output is the assistant message it produced — so the
    fake demo exercises the same structured rendering a real Langfuse trace
    gets. One span is given a deliberately huge tool result: it used to prove
    §6.7 truncation, and now proves the collapsed view copes with a payload
    nobody wants dumped on screen.
    """
    rng = _rng(correlation_id)
    n = rng.randint(5, 8)
    tools = ["read_skill", "sql_query", "sql_query", "vector_search", "summarize",
             "sql_query", "format_response", "generate_response"]
    tool_defs = _fake_tool_defs()
    question = f"(question behind correlation {correlation_id[:8]})"

    system = _FAKE_SYSTEM_PROMPT
    override = _workspace_overrides.get(correlation_id)
    if override is not None:
        # The override has to be *visible in the trace*, not just accepted: that
        # is the only way a developer can confirm the agent used the candidate
        # workspace rather than its own (§10.7). The fake agent puts it where a
        # real one would — in the system prompt of every generation.
        for path, content in (override.skills or {}).items():
            system += f"\n\n# {path} (overridden for this call)\n{content}"

    # The conversation grows span by span, exactly as it does in a real agent
    # loop: every generation sees everything that came before it.
    messages: list[dict] = [
        {"role": "system", "content": system},
        {"role": "user", "content": question},
    ]

    spans: list[Span] = []
    long_span = rng.randint(1, n - 2)  # a middle span gets a huge tool result
    for i in range(n):
        tool = tools[i % len(tools)]
        last = i == n - 1
        out = f"result rows for step {i}: " + ", ".join(f"row{j}" for j in range(4))
        if i == long_span:
            out = ("BEGIN_LONG_OUTPUT " + "x-data-cell " * 400 + "END_LONG_OUTPUT")

        request = {"model": "fake-model-v1", "tools": tool_defs,
                   "messages": [dict(m) for m in messages]}

        if last:
            # Final turn: prose, no tool call.
            assistant = {
                "role": "assistant",
                "content": f"Based on the {i} tool results above, the answer is: {out}",
            }
        else:
            call_id = f"call_{i:02d}"
            assistant = {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": tool,
                        "arguments": json.dumps({"step": i, "tool": tool}),
                    },
                }],
            }

        spans.append(
            Span(
                index=i,
                tool_name=tool,
                status="success",
                input=_dump(request),
                output=_dump(assistant),
                token_usage={"input": rng.randint(80, 400),
                             "output": rng.randint(40, 300),
                             "total": rng.randint(120, 700)},
                input_json=request,
                output_json=assistant,
                # Spread wide enough that the column reads as a measurement
                # rather than as five copies of the same number — the point of
                # showing per-step latency is spotting the one step that took
                # eight times as long as its neighbours, and a fake layer that
                # never produces one cannot exercise that.
                latency_ms=rng.randint(180, 2600),
            )
        )

        messages.append(assistant)
        if not last:
            messages.append({
                "role": "tool",
                "tool_call_id": assistant["tool_calls"][0]["id"],
                "name": tool,
                "content": out,
            })
    return Trace(correlation_id=correlation_id, spans=spans)


def _intended_verdict(question: str) -> str:
    if "⟦wrong⟧" in question:
        return "incorrect"
    # ~30% incorrect by hash, otherwise correct.
    return "incorrect" if _rng(question).random() < 0.30 else "correct"


class FakeAgentClient:
    # REPLACE WITH REAL IMPL: POST the agent server's chat completions endpoint,
    # passing correlation_id as metadata.trace_data.trace_id (§6.2) so the
    # agent applies it to its Langfuse trace.
    async def call(
        self, question: str, correlation_id: str, user_id: str,
        tags: list[str] | None = None,
        workspace: WorkspaceOverride | None = None,
    ) -> AgentResponse:
        if workspace is not None:
            # Remembered so build_fake_trace can show it (_workspace_overrides).
            _workspace_overrides[correlation_id] = workspace
        started = time.monotonic()
        await _sleep_between(fc.AGENT_LATENCY_MIN_S, fc.AGENT_LATENCY_MAX_S)
        # Reported like the real client's: the fake genuinely slept for this long,
        # and a blank latency column in the UI would look like missing plumbing
        # rather than a fake being fake.
        latency_ms = int((time.monotonic() - started) * 1000)
        if "⟦timeout⟧" in question:
            return AgentResponse(
                response="", correlation_id=correlation_id, failed=True,
                error="Simulated agent timeout (⟦timeout⟧ marker).",
                latency_ms=latency_ms,
            )
        verdict = _intended_verdict(question)
        # Encode intended verdict into the response so the fake judge is consistent.
        body = "Here is the agent's answer based on the retrieved data."
        return AgentResponse(
            response=f"[[v:{verdict}]] {body}", correlation_id=correlation_id,
            latency_ms=latency_ms,
        )


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
                       judge_verdict: Verdict | None) -> dict:
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


# The workspace the fake agent "has", shaped like a real one: skills as a flat
# map of file paths.
# Two of the skill directories match the skill tags the seeded eval set uses
# (billing / reporting), so "open an incorrect question in the playground" lands
# on a skill that actually exists in fake mode. `billing` carries a reference
# file because a skill being a *directory* is exactly what the flat-string model
# could not express — the fake has to exercise that too.
_FAKE_SKILL_FILES: dict[str, str] = {
    "billing/SKILL.md": (
        "# Billing skill\n"
        "Invoices, balances, refunds and payment status.\n\n"
        "1. Identify the customer or order the question is about.\n"
        "2. Query the `invoices` table with the SQL tool, filtered to that "
        "customer and the period asked for.\n"
        "3. Sum outstanding balances; never add figures that no tool returned.\n"
        "4. State the amount and the period explicitly in the answer.\n"
        "5. For refunds, read `references/refunds.md` first.\n"
    ),
    "billing/references/refunds.md": (
        "# Refund rules\n"
        "- A refund is only in scope once the invoice is settled.\n"
        "- Partial refunds are prorated by service days, not by amount paid.\n"
    ),
    "reporting/SKILL.md": (
        "# Reporting skill\n"
        "Aggregate reports, trends and churn analysis.\n\n"
        "1. Establish the reporting period before querying anything.\n"
        "2. Retrieve the raw events with the SQL tool, then aggregate.\n"
        "3. Rank the drivers and keep the top three.\n"
        "4. Report each figure with the period it covers.\n"
    ),
    "escalation/SKILL.md": (
        "# Escalation skill\n"
        "Routing a question the other skills cannot answer.\n\n"
        "1. Say plainly which part of the question you cannot answer.\n"
        "2. Name the team that owns it.\n"
        "3. Never guess a figure to avoid escalating.\n"
    ),
}


class FakeSynthesisClient:
    # REPLACE WITH REAL IMPL: app/integrations/real/synthesis.py (§10.8).
    model_name = "fake-synthesis"

    async def synthesize(self, trace: Trace, question: str, agent_response: str) -> str:
        """The same shape a real draft has: numbered steps, one per action.

        Built from the trace rather than canned, so the fake demo shows what the
        button actually does — including that the steps follow the spans the
        developer can see beside them.
        """
        await _sleep_between(fc.DIAGNOSIS_LATENCY_MIN_S, fc.DIAGNOSIS_LATENCY_MAX_S)
        steps = [
            f"{i + 1}. Called `{span.tool_name}` and used what it returned."
            for i, span in enumerate(trace.spans[:-1])
        ]
        steps.append(
            f"{len(steps) + 1}. Produced the final answer presenting "
            f"{(agent_response or 'the result').strip()[:80]}"
        )
        return "\n".join(steps)


class FakeWorkspaceClient:
    # REPLACE WITH REAL IMPL: GET the agent server's skills endpoint.
    async def get_workspace(self) -> Workspace:
        await asyncio.sleep(fc.SKILL_FETCH_LATENCY_S)
        return Workspace(
            version=self._version(),
            # Copied, so an edit made through the API can never mutate the
            # fake's own workspace — the real seam gets a fresh parse per call
            # and the fake must not be quietly more stateful than that.
            skills=dict(_FAKE_SKILL_FILES),
        )

    async def get_version(self) -> str:
        await asyncio.sleep(fc.SKILL_FETCH_LATENCY_S)
        return self._version()

    @staticmethod
    def _version() -> str:
        """Constant while the fake workspace is constant, and derived from it.

        Which means the staleness check is exercised rather than bypassed in
        fake mode: it agrees with itself now, and would disagree the moment the
        canned workspace above changed.

        Its own prefix rather than `derived_version`'s, so a fake-mode version
        is never mistaken for a real agent that simply declined to supply one.
        """
        payload = json.dumps(_FAKE_SKILL_FILES, sort_keys=True).encode()
        return f"fake.{hashlib.sha256(payload).hexdigest()[:7]}"


def _skill_paths(prompt: str) -> list[str]:
    """The skill's file paths, read out of the prompt the optimizer was sent.

    `app/optimizer/skillio.render_skill` writes the directory into the prompt as
    a run of `### File: {path}` headings, and the analyst is told to name one of
    them on every edit. Parsing them back is how the fake proposes edits against
    whichever skill the run is actually optimising instead of a hardcoded one —
    the same list a real model has to work from, so the fake cannot name a file
    the real one could not.
    """
    return [
        line[len("### File:"):].strip()
        for line in prompt.splitlines()
        if line.startswith("### File:") and line[len("### File:"):].strip()
    ]


class FakeOptimizerClient:
    """A deterministic stand-in for the model that writes skill edits.

    Without this the Optimize section could not be demonstrated on Docker alone,
    and "the whole product runs on nothing but Docker" is a property the fake
    layer exists to preserve — every other seam already has a fake.

    Deterministic on the prompt rather than random, for two reasons. A demo that
    produced different edits on every run would make the diff view impossible to
    reason about; and the engine caches validation scores by skill hash
    (upstream's `sel_cache`), so a fake that returned a fresh patch each time
    would never exercise the cache-hit path.

    It answers all three stages the vendored modules ask for — `analyst`,
    `merge`, `ranking` — in their JSON contracts, and it deliberately produces a
    mix of outcomes across steps: an edit to `SKILL.md`, an edit to a reference
    file, and occasionally a patch whose target string does not exist, so the
    "some edits were skipped" path in the diff view has something to show.
    """

    model_name = "fake-optimizer"

    def chat_optimizer(
        self,
        system: str,
        user: str,
        max_completion_tokens: int = 16384,
        retries: int = 3,
        stage: str = "optimizer",
        timeout: int | None = None,
    ) -> tuple[str, dict[str, int]]:
        seed = int(hashlib.sha256(user.encode()).hexdigest()[:8], 16)
        usage = {"prompt_tokens": len(user) // 4, "completion_tokens": 120, "calls": 1}
        # Which files this run is actually allowed to edit, read out of the same
        # "## Current Skill" block a real model reads them from.
        paths = _skill_paths(user)

        if stage == "ranking":
            # Keep the first few in the order they arrived; the real ranker
            # reorders by relevance, which a fake cannot meaningfully imitate.
            return json.dumps({"selected_indices": [0, 1, 2, 3]}), usage

        if stage == "slow_update":
            # The epoch-boundary pass. Deterministic like the rest, and phrased
            # as guidance rather than as an edit, because that is what it is:
            # free-form advice written into a protected block that step-level
            # analysts may read but not change.
            return json.dumps({
                "reasoning": "fake slow update: regressions clustered on refunds",
                "slow_update_content": (
                    "Across this epoch, answers improved when the period was "
                    "stated before the figure. Prefer tightening an existing "
                    "rule over adding a new one."
                ),
            }), usage

        if stage == "meta_skill":
            return json.dumps({
                "reasoning": "fake meta skill: narrower edits landed more often",
                "meta_skill_content": (
                    "Edit one rule at a time; batched rewrites were rejected "
                    "more often than they were accepted."
                ),
            }), usage

        if stage == "merge":
            return json.dumps({
                "reasoning": "fake merge: near-duplicate edits collapsed",
                "edits": self._edits(seed, paths),
            }), usage

        # The analyst stage, which also carries the failure summary Part 1 shows.
        return json.dumps({
            "batch_size": user.count("Question:") or 1,
            "failure_summary": [
                {
                    "failure_type": "rule_missing",
                    "count": 2,
                    "description": "no rule about stating the period alongside the figure",
                },
                {
                    "failure_type": "answer_format",
                    "count": 1,
                    "description": "the currency was omitted from the amount",
                },
            ],
            "patch": {
                "reasoning": "fake analyst: two common patterns across the minibatch",
                "edits": self._edits(seed, paths),
            },
        }), usage

    @staticmethod
    def _edits(seed: int, paths: list[str]) -> list[dict]:
        """Edits aimed at the skill actually being optimised.

        These paths used to be the literals `billing/SKILL.md` and
        `billing/references/refunds.md`, which made the fake correct for exactly
        one skill and a liar for every other one. Optimising `reporting` sent
        every edit at `billing/`, the patcher refused all of them as
        `skipped_invalid_path`, the candidate came out identical to its parent,
        the validation rollout was skipped as a cache hit, and the diff view
        said the step had changed nothing — four symptoms, one hardcoded string,
        and a demo that appeared to be editing a skill it had never been given.

        `paths` comes from the prompt's own "## Current Skill" listing, which is
        where a real model is told what it may name, so the fake now works from
        the same evidence rather than from an assumption.
        """
        rng = random.Random(seed)
        rule = rng.randint(100, 999)
        if not paths:
            # No file list in the prompt (a stage that is not shown one).
            # Proposing a path that was never offered is what the bug above did.
            return []
        entry = next((p for p in paths if p.endswith("SKILL.md")), paths[0])
        # A second file when the skill has one, so the multi-file diff stays
        # reachable; the entry point again when it does not.
        other = next((p for p in paths if p != entry), entry)
        edits = [
            {
                "op": "append",
                "path": entry,
                "content": f"{rule}. State the period alongside every figure.",
            },
            {
                "op": "append",
                "path": other,
                "content": f"- Rule {rule}: quote the currency with the amount.",
            },
        ]
        if seed % 3 == 0:
            # A target that will not be found, so the diff view's "proposed but
            # not applied" section is reachable in the demo. An anchor that does
            # not exist is a real failure a real model makes; an out-of-skill
            # path is not — it is the bug this docstring is about.
            edits.append({
                "op": "replace",
                "path": entry,
                "target": "a line this skill does not contain",
                "content": "unreachable",
            })
        return edits
