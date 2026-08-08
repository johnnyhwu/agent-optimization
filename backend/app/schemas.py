"""Pydantic request/response models."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# --- Eval sets --------------------------------------------------------------

class ShareEntry(BaseModel):
    """One access grant on an eval set (§6.16 roles)."""
    subject: str
    role: str  # 'owner' | 'viewer'


class EvalSetCreate(BaseModel):
    name: str
    description: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    # Optional access grants beyond the creator (who is always owner).
    shares: list[ShareEntry] = Field(default_factory=list)
    # Questions, always serialized as JSONL. A CSV upload is parsed and converted
    # to JSONL in the browser (§9.1), so the wire contract stays JSONL-only.
    jsonl: str
    # Which format the developer actually uploaded — recorded on the eval set for
    # provenance (§6.14 `source_format`). The payload above is JSONL either way.
    source_format: Literal["csv", "jsonl"] = "jsonl"


class EvalSetUpdate(BaseModel):
    """Edit name/description/metadata/judge prompt under optimistic lock (§6.16).

    The judge prompt rides the existing owner-only, versioned PATCH rather than
    getting an endpoint of its own — it is a property of the set like the others,
    and reusing this route means it inherits the 409 conflict flow for free.
    An empty string is meaningful here: it clears the override and returns the
    set to the default prompt. `None` (the field absent) leaves it untouched.
    """
    name: str | None = None
    description: str | None = None
    metadata: dict[str, str] | None = None
    judge_system_prompt: str | None = None
    judge_user_prompt: str | None = None
    version: int  # client-held version; mismatch -> 409


class JudgePromptOut(BaseModel):
    """This eval set's grading criteria, as the Judging tab renders them.

    `system_prompt` / `user_prompt` are the *effective* text — the override or
    the default, already resolved — because a textarea has to show something and
    "empty means the default you can't see" is not something to make a person
    reason about. `is_default` is what says which of the two it is.
    """
    system_prompt: str
    user_prompt: str
    is_default: bool
    fingerprint: str
    # Placeholders the user template is missing. Empty is the healthy state; the
    # UI blocks nothing on it but says loudly what will happen.
    missing_placeholders: list[str] = Field(default_factory=list)
    verified_at: datetime | None = None
    verified_model: str | None = None
    reviewed_at: datetime | None = None


class JudgePromptVerifyRequest(BaseModel):
    """Grade one known question twice, to see whether this prompt still works.

    `question_pk` is the question to try it on — the developer picks, because
    only they know which of their questions is representative.

    `model` and `api_key` are optional overrides. Left blank, the server uses the
    environment's LLM settings, which is also what a run with a blank config
    would use. The key is inbound-only, exactly like `RunSecrets`.
    """
    question_pk: uuid.UUID
    system_prompt: str
    user_prompt: str
    model: str = ""
    api_key: str = ""


class JudgePromptVerifyCase(BaseModel):
    """One of the two graded probes."""
    label: str  # 'ground truth as the answer' | 'a deliberately wrong answer'
    expected_verdict: str
    ok: bool
    verdict: str | None = None
    score: float | None = None
    comment: str | None = None
    error: str | None = None


class JudgePromptVerifyResult(BaseModel):
    """Both probes plus the one-line answer.

    Two calls rather than one, because a single successful parse only proves the
    reply was JSON. A prompt that answers "correct" to everything also parses
    perfectly — and it would take a whole run, and a pass rate of 100%, to notice.
    """
    ok: bool
    model: str
    missing_placeholders: list[str] = Field(default_factory=list)
    cases: list[JudgePromptVerifyCase] = Field(default_factory=list)


class RolesUpdate(BaseModel):
    """Replace the share list for an eval set (owner-only)."""
    shares: list[ShareEntry] = Field(default_factory=list)


class RunTrend(BaseModel):
    run_id: uuid.UUID
    pass_rate: float | None
    started_at: datetime


class Page(BaseModel):
    """One slice of a list, plus what the UI needs to ask for the next.

    `total` is what lets the page say "24 of 137" rather than only offering a
    Load-more button with no sense of how much is left; `has_more` is computed
    server-side so the client never has to reason about the arithmetic.
    """
    total: int
    has_more: bool


class EvalSetPage(Page):
    items: list["EvalSetCard"]


class EvalSetCard(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    metadata: dict
    version: int
    created_at: datetime
    updated_at: datetime
    run_count: int
    question_count: int
    latest_pass_rate: float | None
    trend: list[float | None]  # ordered oldest->newest pass rates (sparkline)
    regressed: int
    improved: int
    my_role: str | None
    roles: list[ShareEntry]  # current share list (for the config dialog)
    # Grading criteria for every run of this set. Readable by any role on
    # purpose: a viewer whose run comes back 40% is entitled to know what "wrong"
    # meant. Only an owner can change it.
    judge_prompt: JudgePromptOut


# --- Questions --------------------------------------------------------------

class QuestionOut(BaseModel):
    id: uuid.UUID
    question_id: str
    question: str
    ground_truth_response: str
    ground_truth_reasoning: str
    skills: list[str]
    version: int


class QuestionUpdate(BaseModel):
    """Edit question text (locked set: no add/delete). question_id is immutable."""
    question: str | None = None
    ground_truth_response: str | None = None
    ground_truth_reasoning: str | None = None
    version: int  # optimistic lock; mismatch -> 409


# --- Runs / results ---------------------------------------------------------

class RunConfig(BaseModel):
    """The non-secret settings a run is triggered with (§9.2 seams).

    Every field is optional: a blank value means "use the environment", which is
    what keeps the seeded fake demo runnable from an empty form. Defaults are
    served to the UI by GET /run-config/defaults rather than baked in here, so
    the form and the fallback always agree.
    """

    agent_base_url: str = ""
    agent_timeout_s: float | None = None
    langfuse_host: str = ""
    langfuse_public_key: str = ""
    langfuse_timeout_s: float | None = None
    llm_base_url: str = ""
    judge_model: str = ""
    diagnosis_model: str = ""
    # How many questions are sent to the agent at once.
    concurrency: int | None = Field(default=None, ge=1)
    # The grading criteria this run used, frozen at trigger time. Present on the
    # way out (and in exports) but **ignored on the way in**: `trigger_run`
    # overwrites all three from the eval set, which is what makes "only an owner
    # can change how answers are graded" true no matter what a client posts.
    judge_system_prompt: str = ""
    judge_user_prompt: str = ""
    # Short hash of the pair above. Two runs sharing it were graded by the same
    # words, so their pass rates are comparable — the run list shows it for
    # exactly that reason.
    judge_prompt_fingerprint: str = ""


class RunSecrets(BaseModel):
    """Credentials for one run. Inbound only — no response model carries these."""

    langfuse_secret_key: str = ""
    llm_api_key: str = ""


class RunCreate(BaseModel):
    """Body of POST /eval-sets/{id}/runs."""

    name: str | None = None
    config: RunConfig = Field(default_factory=RunConfig)
    secrets: RunSecrets = Field(default_factory=RunSecrets)
    # Borrow the credentials of an earlier run instead of retyping them. They are
    # copied server-side and never travel to the browser; a credential is only
    # copied when its paired endpoint is unchanged (see routers/runs.py).
    reuse_secrets_from_run_id: uuid.UUID | None = None


class RunOut(BaseModel):
    id: uuid.UUID
    eval_set_id: uuid.UUID
    triggered_by: str
    name: str | None = None
    # Non-secret settings only: RunConfig has no credential fields, so this can
    # never carry one outward.
    config: RunConfig = Field(default_factory=RunConfig)
    # Which credential slots this run recorded — slot *names* ("llm",
    # "langfuse"), never values. Enough to diagnose "the judge failed because no
    # LLM key was set" without putting a credential on the wire.
    credentials_set: list[str] = Field(default_factory=list)
    status: str  # running | completed | failed | cancelled
    # True once someone hit stop, even before the run has finished winding down.
    cancel_requested: bool = False
    started_at: datetime
    completed_at: datetime | None
    pass_rate: float | None
    total_count: int | None
    correct_count: int | None
    incorrect_count: int | None = None
    # Questions whose judge replied with something we could not parse. Reported
    # separately from the pass rate rather than folded into it: they stay in the
    # denominator (an ungraded question is not a pass), but a rate that dropped
    # because the judge broke is a different problem from one that dropped
    # because the agent got worse, and the number is the only thing that says so.
    judge_invalid_count: int | None = None


class RunPage(Page):
    items: list[RunOut]


class QuestionResultOut(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    # Which run this row came from, in words. In a multi-run selection the row
    # shown is a *representative* one that may belong to an older run than the
    # one being watched — without a label, an old run's trace and errors are
    # easily mistaken for the live run's.
    run_label: str | None = None
    question_pk: uuid.UUID
    question_id: str
    question: str
    correlation_id: str
    agent_response: str | None = None  # what the agent actually answered
    verdict: str | None
    judge_score: float | None
    judge_comment: str | None
    status: str  # pending | done | failed | cancelled
    # How far this question got — 'pending' | 'answered' | 'judged' | 'failed' |
    # 'judge_invalid' | 'cancelled'. Derived server-side
    # (services.aggregation.result_phase) so the left column's colours and the
    # live SSE events come from one rule.
    phase: str
    error_message: str | None = None  # why status == 'failed' / 'cancelled'
    failure_kind: str | None = None  # 'agent' | 'judge' | 'judge_invalid'
    agent_latency_ms: int | None = None
    trace_ready: bool
    has_analysis: bool
    is_incorrect: bool  # per the requested multi-run mode


class SpanOut(BaseModel):
    index: int
    tool_name: str
    status: str
    # The span body exactly as the trace store held it: an object/array when the
    # agent logged something structured (an LLM call's `{tools, messages}` and
    # the assistant message it produced), a plain string otherwise.
    #
    # Never truncated. §6.7's cut still applies before the diagnosis LLM, where
    # a context window is the constraint — but cutting it here destroyed the
    # very evidence the span view exists to show, and left the JSON unparseable
    # for the UI. The UI collapses long bodies instead.
    input: Any = ""
    output: Any = ""
    token_usage: dict
    status_message: str | None = None  # Langfuse statusMessage on ERROR spans


class SuspectOut(BaseModel):
    span_index: int
    confidence: str
    reason: str
    evidence: str


class AnalysisOut(BaseModel):
    overall_diagnosis: str
    caveat: str | None
    suspects: list[SuspectOut]
    generated_at: datetime
    model_used: str


class TraceView(BaseModel):
    """Middle+right column payload for one question_result."""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    # 'ready'       spans below
    # 'generating'  the agent answered; ingestion hasn't landed yet (§6.12)
    # 'not_started' the agent hasn't been asked yet — nothing to fetch, and the
    #               trace store is deliberately not called
    # 'no_trace'    the question failed or was cancelled before answering
    # 'error'       the trace store could not be read
    trace_state: str
    # Why the trace could not be fetched. 'error' always carries one; 'generating'
    # carries the run-time failure when there was one, so a developer staring at
    # "still ingesting" can see that the last attempt actually got a 401.
    trace_error: str | None = None
    # Why this question has no diagnosis, when the LLM call was made and failed.
    diagnosis_error: str | None = None
    spans: list[SpanOut] = Field(default_factory=list)
    analysis: AnalysisOut | None = None
    verdict: str | None = None
    judge_comment: str | None = None
    # The answer under evaluation, next to what it was graded against — with a
    # real agent this is the first thing a developer wants to read.
    agent_response: str | None = None
    ground_truth_response: str | None = None
    # The expected process the diagnosis was made against. Carried so the view can
    # hand a question over to the playground (§10.5) with the same expectations
    # attached — the hypothesis being tested was formed while reading this trace.
    ground_truth_reasoning: str | None = None
    error_message: str | None = None


# --- Playground (§10) -------------------------------------------------------

class WorkspaceOut(BaseModel):
    """The agent server's config + skill files, as the editor starts from."""

    version: str = ""
    config: dict = Field(default_factory=dict)
    # Config paths the agent server withheld (its own API keys). Shown as
    # present-but-hidden rather than dropped: a field that vanishes silently
    # invites someone to re-add it by hand and shadow the real value.
    redacted_paths: list[str] = Field(default_factory=list)
    # Flat {relative path: file text} — a skill is a directory, so `SKILL.md`
    # and everything under `references/` arrive as separate entries.
    skills: dict[str, str] = Field(default_factory=dict)


class WorkspaceVersionOut(BaseModel):
    """Just the version, for the staleness check made before each send."""

    version: str = ""


class WorkspaceOverrideIn(BaseModel):
    """The config/skills to use for this one call instead of the agent's own.

    The two halves travel differently on purpose (see
    docs/spec.md §17.4): `config` is sparse and is
    deep-merged on the agent server — it must be, since the snapshot the editor
    started from had the agent's secrets removed — while `skills` is the
    complete file set for the call, because only replacement can express
    deleting a file.

    Both are optional: editing only the config, or only a skill, is the common
    case, and sending the untouched half would claim an edit that never
    happened.
    """

    config: dict | None = None
    skills: dict[str, str] | None = None

    @property
    def is_empty(self) -> bool:
        return not self.config and self.skills is None


class PlaygroundCreate(BaseModel):
    """Body of POST /playground/attempts.

    Only `question` is required. The two ground-truth fields are switches, not
    paperwork: an expected answer turns judging on, an expected reasoning process
    turns diagnosis on (§10.4). A developer trying a question out often has
    neither, and demanding them would defeat the point.
    """

    question: str = Field(min_length=1)
    ground_truth_response: str | None = None
    ground_truth_reasoning: str | None = None
    workspace: WorkspaceOverrideIn | None = None
    # Same per-run settings the eval path uses, so an attempt can target the same
    # endpoints a given run did.
    config: RunConfig = Field(default_factory=RunConfig)
    secrets: RunSecrets = Field(default_factory=RunSecrets)


class SynthesisOut(BaseModel):
    """A drafted expected process, plus which model drafted it.

    Not stored on the attempt: it is a starting point for the shortlist's editor
    (§10.8), and the developer is expected to change it before it becomes a
    question's ground truth.
    """

    reasoning_process: str
    model_used: str


class ShortlistQuestion(BaseModel):
    """One question a developer promoted out of the playground (§10.8).

    All three fields are required because `questions` requires all three — an
    eval set with a blank expected process would be un-diagnosable, so the
    shortlist dialog cannot submit until they are filled in.
    """

    question: str = Field(min_length=1)
    ground_truth_response: str = Field(min_length=1)
    ground_truth_reasoning: str = Field(min_length=1)
    skills: list[str] = Field(default_factory=list)


class EvalSetFromShortlist(BaseModel):
    """Body of POST /eval-sets/from-shortlist.

    Same identity fields as a normal upload, plus the two sources of questions:
    the shortlisted ones, and whole eval sets to copy questions out of. The
    second exists because an eval set is locked after creation (§4.6) — the only
    way to end up with "the old questions plus these new ones" is to build a new
    set from both.
    """

    name: str = Field(min_length=1)
    description: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    shares: list[ShareEntry] = Field(default_factory=list)
    questions: list[ShortlistQuestion] = Field(default_factory=list)
    # Copied in listed order, after the shortlisted questions.
    include_eval_set_ids: list[uuid.UUID] = Field(default_factory=list)


class EvalSetFromShortlistOut(BaseModel):
    id: uuid.UUID
    question_count: int
    # Questions dropped because an identical question text was already in the
    # new set. Reported rather than silently skipped: "I selected two sets and
    # got fewer questions than they hold" needs an answer on screen.
    duplicates_skipped: int = 0


class PlaygroundAttemptOut(BaseModel):
    """One attempt, as the list and the header show it."""

    id: uuid.UUID
    created_at: datetime
    question: str
    # Whether each optional stage applies at all, so the UI can say "not judged"
    # rather than leaving an empty verdict looking like a failure.
    has_expected_answer: bool
    has_expected_reasoning: bool
    # What the call carried, for the attempt list's summary. The platform cannot
    # verify the agent actually applied any of it (§10.7) — these only report
    # what was sent.
    workspace_overridden: bool = False
    # Dotted config paths this attempt overrode, e.g. ["agents.defaults.model"].
    config_overrides: list[str] = Field(default_factory=list)
    # Skill files whose text differed from the agent's own, plus any it deleted.
    edited_skill_files: list[str] = Field(default_factory=list)
    status: str  # running | done | failed | cancelled
    phase: str  # pending | answered | judged | traced | diagnosed
    verdict: str | None = None
    judge_score: float | None = None
    # When the agent call went out, and how long it took. The list counts up from
    # the first while the attempt is running and shows the second once it lands —
    # so the two together are "how long has this been going" and "how long did it
    # take", from one server-side clock rather than the browser's.
    agent_started_at: datetime | None = None
    agent_latency_ms: int | None = None
    error_message: str | None = None
    # Non-secret settings only: RunConfig has no credential fields.
    config: RunConfig = Field(default_factory=RunConfig)


class PlaygroundAttemptDetail(PlaygroundAttemptOut):
    """One attempt plus its trace, in the same shape the run detail view uses.

    Reusing `TraceView` is the point: the middle and right columns are the same
    components, so the playground gets structured span rendering, the diagnosis
    banners and the five trace states without a second implementation.
    """

    ground_truth_response: str | None = None
    ground_truth_reasoning: str | None = None
    # The override exactly as it was sent, so cloning an attempt back into the
    # composer reproduces it. A clone that silently dropped it would change two
    # variables at once, which is what makes a before/after comparison
    # worthless.
    workspace: WorkspaceOverrideIn | None = None
    trace: TraceView
