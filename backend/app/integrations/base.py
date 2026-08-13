"""The six integration seams as Protocols + shared data types (§6.15, §10.2).

A real implementation swaps in behind the SAME interface — the orchestrator and
routers depend only on these Protocols, never on a concrete module.

Seams:
    AgentClient.call(question, correlation_id, user_id, tags, workspace) -> AgentResponse
    JudgeClient.judge(question, response, ground_truth)               -> Verdict
    TraceClient.fetch_trace(correlation_id)                           -> Trace | NotReady
    DiagnosisClient.diagnose(trace, ground_truth_reasoning, verdict)  -> dict (§6.9 JSON)
    WorkspaceClient.get_workspace() / .get_version()                  -> the agent's config + skills
    SynthesisClient.synthesize(trace, question, response)             -> a draft expected process
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


# --- Shared errors ----------------------------------------------------------

class LlmOutputError(RuntimeError):
    """The model replied, but not with the JSON contract we asked for.

    Defined here rather than beside the OpenAI client that raises it, because
    the orchestrator has to name it in an `except` clause — and `real/llm.py` is
    imported lazily so that a fake-only deployment never touches a real seam.
    `real.llm` re-exports it, so the raising side reads unchanged.
    """


# --- Shared value objects ---------------------------------------------------

@dataclass
class AgentResponse:
    response: str
    correlation_id: str
    failed: bool = False  # agent timeout/error -> question status=failed
    # Why it failed, surfaced in the UI. A bare status='failed' tells the
    # developer nothing once the agent is a real service.
    error: str | None = None
    latency_ms: int | None = None


@dataclass
class Verdict:
    verdict: str  # 'correct' | 'incorrect'
    score: float
    comment: str | None = None


@dataclass
class Span:
    """One Langfuse observation, reconstructed for the UI (§2.3 / §6.9)."""
    index: int
    tool_name: str
    status: str
    input: str
    output: str
    token_usage: dict = field(default_factory=dict)  # {"input": n, "output": n, "total": n}
    # Langfuse `statusMessage`: why an observation is at ERROR level. Only ever
    # populated by the real trace client.
    status_message: str | None = None
    # The body as the trace store actually held it, when it was structured — an
    # LLM generation's `{"tools": [...], "messages": [...]}` request and the
    # assistant message it produced. The UI renders that per message instead of
    # dumping JSON; `input`/`output` above stay text because the diagnosis
    # prompt is built from them.
    input_json: object | None = None
    output_json: object | None = None


@dataclass
class Trace:
    correlation_id: str
    spans: list[Span]


@dataclass
class Workspace:
    """What the agent server is currently configured with (§10.2).

    A skill is not a blob of text: on the agent server it is a directory
    (`SKILL.md` plus whatever `references/` it carries), and half of what
    decides the agent's behaviour lives in `config.json` next to it. So the
    playground reads the whole thing at once — one request, one consistent
    snapshot, no chance of pairing this minute's config with last minute's
    skills.
    """

    # Changes whenever config.json or any skill file changes, so an edit that
    # started from a stale snapshot can be spotted before it is sent.
    version: str
    # config.json, nested exactly as the agent server holds it, minus the
    # secrets — the platform must never receive the agent's own API keys.
    config: dict
    # Dotted paths the agent server removed, e.g. "agents.defaults.api_key".
    # Kept so the UI can show the field as present-but-hidden: a field that
    # vanishes silently invites someone to re-add it and shadow the real key.
    redacted_paths: list[str] = field(default_factory=list)
    # Flat {relative path: file text}, e.g. "billing/references/refunds.md".
    skills: dict[str, str] = field(default_factory=dict)


@dataclass
class WorkspaceOverride:
    """The config/skills one agent call should use instead of the server's own.

    The playground's whole point (§4.7 / §6.5): try an edited skill or a
    different setting without writing anything back to the agent server.

    The two halves are sent — and applied — differently, and the asymmetry is
    deliberate (see docs/spec.md §17.4):

      * `config` is a **sparse** overlay, deep-merged onto the agent's own
        config.json. It has to be: the snapshot arrived with its secrets
        stripped, so replacing the file wholesale would leave the agent with no
        API key at all.
      * `skills` is the **complete** file set for the call, replacing the
        server's directory. Only replacement can express deleting a file, which
        is a legitimate experiment ("does it still work without this
        reference?").

    Either half may be None, meaning "use the agent server's own".
    """

    config: dict | None = None
    skills: dict[str, str] | None = None

    @property
    def edited_config_paths(self) -> list[str]:
        """Dotted paths this override changes, for the UI's summary line."""
        return sorted(_leaf_paths(self.config or {}))


def _leaf_paths(node: dict, prefix: str = "") -> list[str]:
    out: list[str] = []
    for key, value in node.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict) and value:
            out.extend(_leaf_paths(value, f"{path}."))
        else:
            out.append(path)
    return out


class NotReady:
    """Sentinel: Langfuse ingestion hasn't landed the trace yet (§6.12)."""


NOT_READY = NotReady()


class TraceFetchError(RuntimeError):
    """The trace store could not be reached or refused the request.

    Deliberately distinct from `NotReady`: "your Langfuse host is wrong" and
    "ingestion hasn't landed yet" produce the same empty result otherwise, and
    collapsing them is what makes a misconfigured deployment look like a trace
    that is perpetually seconds away.

    `partial` marks the in-between case: one read path failed, but another one
    answered "the trace isn't there yet". The failure is real and worth showing,
    yet it is *not* proof that this trace will never arrive — so the caller
    should keep polling and report it as context, not as a dead end.
    """

    def __init__(self, *args: object, partial: bool = False) -> None:
        super().__init__(*args)
        self.partial = partial


# --- Protocols (the swappable seams) ----------------------------------------

@runtime_checkable
class AgentClient(Protocol):
    # `user_id` is the subject who triggered the run; `tags` lets the caller
    # attach labels (e.g. the eval set name) to the agent's Langfuse metadata.
    # `workspace` is keyword-with-a-default on purpose: an eval run never sends
    # one, so the run path is untouched by the playground existing.
    async def call(
        self, question: str, correlation_id: str, user_id: str,
        tags: list[str] | None = None,
        workspace: "WorkspaceOverride | None" = None,
    ) -> AgentResponse: ...


@runtime_checkable
class JudgeClient(Protocol):
    # `question` is part of the contract because a real LLM judge needs the
    # question itself to grade an answer against the ground truth.
    async def judge(self, question: str, response: str, ground_truth: str) -> Verdict: ...


@runtime_checkable
class TraceClient(Protocol):
    async def fetch_trace(self, correlation_id: str) -> "Trace | NotReady": ...


@runtime_checkable
class DiagnosisClient(Protocol):
    # `model_name` is stored on span_analyses.model_used, so every implementation
    # exposes which model produced a diagnosis.
    model_name: str

    # `judge_verdict` is optional because the playground allows an expected
    # reasoning process with no expected answer (§10.4): there is a flow to
    # compare the trace against, but nothing was graded. An eval run always has
    # a verdict — it only diagnoses questions the judge marked incorrect.
    async def diagnose(
        self, trace: Trace, ground_truth_reasoning: str,
        judge_verdict: Verdict | None,
    ) -> dict: ...


@runtime_checkable
class SynthesisClient(Protocol):
    """Draft an expected reasoning process from a trace the agent produced.

    Offered on a button, never run automatically (§10.8): the output describes
    what the agent did, and turning that into what is *expected* is a judgement
    only the developer can make. Synthesising for every attempt would also be a
    real LLM bill for drafts nobody asked for.
    """

    model_name: str

    async def synthesize(self, trace: Trace, question: str, agent_response: str) -> str: ...


class OptimizerClient(Protocol):
    """The model that turns scored rollouts into skill edits (Optimize, §Stage 3).

    SkillOpt's "optimizer" role: it reflects on a minibatch of trajectories,
    merges the resulting patches, and ranks them against a learning-rate budget.
    Distinct from the judge (which scores one answer) and from the diagnosis
    model (which localises one failure to a span) — this one reasons over a whole
    batch and outputs edits.

    **This seam is synchronous, and it is the only one that is.** The vendored
    SkillOpt modules that call it are synchronous and parallelise themselves with
    a thread pool, so the engine runs that whole stage inside `asyncio.to_thread`
    (see `app/optimizer/VENDORED.md`). Making this `async` would mean re-entering
    the event loop from a worker thread — the one thing that arrangement exists
    to avoid. The signature matches upstream's `chat_optimizer` exactly, so the
    vendored files differ from upstream by one import line each.
    """

    model_name: str

    def chat_optimizer(
        self,
        system: str,
        user: str,
        max_completion_tokens: int = 16384,
        retries: int = 3,
        stage: str = "optimizer",
        timeout: int | None = None,
    ) -> tuple[str, dict[str, int]]: ...


@runtime_checkable
class WorkspaceClient(Protocol):
    """Read the agent's config and skill files, so the playground can edit from
    the real starting point rather than from a blank textarea (§10.2).

    Read-only by design: writing an optimized skill back to the agent server
    needs versioning and rollback (§4.9) and belongs to Stage 3.
    """

    async def get_workspace(self) -> Workspace: ...

    # Just the version string. Cheap enough to call before every send, which is
    # what makes "your snapshot is stale" a question the developer gets asked
    # before the experiment rather than a mystery afterwards.
    async def get_version(self) -> str: ...
