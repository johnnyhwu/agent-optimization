"""Pydantic request/response models."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


# --- Eval sets --------------------------------------------------------------

class ShareEntry(BaseModel):
    """One access grant on an eval set (§6.16 roles)."""
    subject: str
    role: str  # 'owner' | 'viewer'


class ScriptProvenance(BaseModel):
    """How a script-built eval set was produced, stored alongside it.

    `extra="forbid"` is the point of the model, not a detail: the obvious way for
    a password to end up in the database is for a client to helpfully include the
    connection it used, password and all, and for the server to accept the fields
    it recognises and silently store the rest. A payload carrying one is refused.
    """

    model_config = ConfigDict(extra="forbid")

    source: str
    db_host: str
    db_port: int
    db_name: str
    db_user: str


class EvalSetCreate(BaseModel):
    name: str
    description: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    # Optional access grants beyond the creator (who is always owner).
    shares: list[ShareEntry] = Field(default_factory=list)
    # Questions, always serialized as JSONL. A CSV upload is parsed and converted
    # to JSONL in the browser (§9.1), so the wire contract stays JSONL-only — and
    # a script's rows arrive the same way, so everything below this line is
    # identical no matter where the questions came from.
    jsonl: str
    # Which format the developer actually uploaded — recorded on the eval set for
    # provenance (§6.14 `source_format`). The payload above is JSONL either way.
    source_format: Literal["csv", "jsonl", "python"] = "jsonl"
    # Only read when source_format is "python"; ignored otherwise, so a confused
    # client cannot attach a script to a set no script produced.
    script: ScriptProvenance | None = None


# --- Script upload ----------------------------------------------------------

class ScriptSource(BaseModel):
    source: str


class ScriptTarget(BaseModel):
    """The database a script reads from, for the duration of one request.

    Never stored, never logged, never returned. `ScriptProvenance` above is the
    subset that is allowed to be written down, and it has no password field.
    """

    host: str
    port: int = 5432
    database: str
    user: str
    password: str = ""


class ScriptRunRequest(BaseModel):
    source: str
    connection: ScriptTarget


class ScriptLimitsOut(BaseModel):
    """The ceilings a script run is actually held to, on this deployment.

    Served because they are otherwise invisible until one of them fires, and a
    limit you meet for the first time in an error message is a limit you have no
    way to check you have configured. Every value here is a deployment setting
    (`SCRIPT_*`, see `app/config.py`), so the answer to "I raised it and nothing
    changed" is one page refresh rather than a code search.
    """

    max_rows_per_query: int
    statement_timeout_s: int
    wall_clock_s: int
    max_queries: int
    max_output_chars: int
    memory_mb: int


class ScriptCheckOut(BaseModel):
    id: str
    label: str
    status: Literal["pass", "warn", "fail", "skipped"]
    detail: str = ""


class ScriptValidationOut(BaseModel):
    ok: bool
    checks: list[ScriptCheckOut]


class ScriptRunOut(BaseModel):
    """Everything the upload dialog needs to render one run.

    `rows` uses the same wire names as a JSONL upload, so the browser maps them
    with the parser it already has and the preview cannot tell the two apart.
    """

    ok: bool
    checks: list[ScriptCheckOut]
    rows: list[dict] = Field(default_factory=list)
    # Per-row problems: the rows above are the ones that survived.
    warnings: list[str] = Field(default_factory=list)
    # System ceilings the run bumped into (row caps, output cap). Shown as
    # banners, not list items — they are about our limits, not the user's data.
    limits_hit: list[str] = Field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    traceback: str = ""
    duration_ms: int = 0
    query_count: int = 0


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
    """Edit a question (locked set: no add/delete). question_id is immutable."""
    question: str | None = None
    ground_truth_response: str | None = None
    ground_truth_reasoning: str | None = None
    # The upload's fourth column, editable here for the same reason as the other
    # three: the file is not a document anyone keeps, and a tag typed as
    # "billling" is otherwise only fixable by re-uploading the whole set.
    #
    # `None` means "leave the tags alone", as it does for every field above it.
    # `[]` is a real value — it clears them — and is not the same thing: a
    # question with no tag is one the optimizer files under `ambiguous`, which is
    # an existing state (sets promoted from a shortlist arrive that way) rather
    # than a state this endpoint invents.
    skills: list[str] | None = None
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


# The longest name the two list surfaces will render without truncating, and the
# same number the browser checks against (`frontend/src/run_name.js`). Named once
# so the two cannot drift into a form that accepts what the server refuses.
RUN_NAME_MAX_LENGTH = 120


def _clean_run_name(value: str | None) -> str | None:
    """A run name as it is stored: trimmed, blank meaning none.

    Blank is a value, not an error — clearing the name is how a rename is undone,
    and both list surfaces fall back to the run's start time when it is unset.
    Control characters are refused because a name made of them renders as
    nothing: the row would read as unnamed while the database held a value, with
    nothing on screen able to explain the difference.
    """
    if value is None:
        return None
    name = value.strip()
    if not name:
        return None
    if len(name) > RUN_NAME_MAX_LENGTH:
        raise ValueError(
            f"name is {len(name)} characters; the limit is {RUN_NAME_MAX_LENGTH}"
        )
    if any(ch < " " or ch == "\x7f" for ch in name):
        raise ValueError("name may not contain line breaks or control characters")
    return name


class RunRename(BaseModel):
    """Body of PATCH .../runs/{id} — the run's name and nothing else.

    Deliberately its own model rather than a partial `RunCreate`: a run's config
    and credentials describe what it was executed with and are not editable
    after the fact, and a PATCH that accepted them would silently promise an
    edit the orchestrator would never see.
    """

    name: str | None = None

    @field_validator("name")
    @classmethod
    def _check(cls, value: str | None) -> str | None:
        return _clean_run_name(value)


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
    # 'agent' | 'agent_timeout' | 'judge' | 'judge_timeout' | 'judge_invalid'.
    # The *_timeout kinds carry no extra state — the message says what happened;
    # this is what lets the list mark them without reading it. NULL on rows
    # written before the column existed.
    failure_kind: str | None = None
    # When the agent call went out. NULL both for a question that has not
    # started and for one written before the column existed — the list shows no
    # timer for either, which beats inventing a duration for old runs.
    started_at: datetime | None = None
    agent_latency_ms: int | None = None
    # How many model calls this question cost, counted from its trace as the run
    # executed. NULL for runs that finished before it was recorded, and for a
    # question whose trace never arrived — neither is "the agent made no calls".
    llm_call_count: int | None = None
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
    # This one step's own duration, from the trace store's two ends. The
    # question already reports how long it took in total; this is what says
    # whether that was one slow model call or a tool that hung. None when the
    # store gave only one end, or none.
    latency_ms: int | None = None


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
    # Which step failed, so the column can explain a timeout as a timeout rather
    # than quoting the sentence back as a generic error. Same vocabulary as
    # QuestionResultOut.failure_kind.
    failure_kind: str | None = None


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
    # Which step failed, in the same vocabulary a run's results use, so the
    # attempt list can mark a timeout without parsing the message.
    failure_kind: str | None = None
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


# --- Optimize (Stage 3) -----------------------------------------------------
#
# No model here has a field that could carry a credential outward, exactly as
# `RunOut` has none: `optimization_runs.secrets` is a separate column and
# nothing below reads it. That is what makes "keys never leave the server"
# structural rather than a habit somebody has to remember.


class OptimizationStepSummary(BaseModel):
    """One row of the chart. The overview fetches every step in one payload.

    `train_*` is measured with the skill as it *entered* the step; `val_*` with
    the candidate the step produced. They are two different skills, which is why
    the chart draws train half a step to the left — and why they are named apart
    rather than sharing a `hard`.
    """

    step_no: int
    epoch_no: int
    step_in_epoch: int
    parent_step_no: int | None = None
    status: str
    gate_action: str | None = None
    gate_reject_reason: str | None = None
    retried: bool = False
    abort_reason: str | None = None

    train_hard: float | None = None
    train_soft: float | None = None
    train_activation_rate: float | None = None
    train_n_scored: int | None = None
    train_n_items: int | None = None
    train_n_agent_error: int | None = None
    train_n_judge_error: int | None = None
    train_latency_min_ms: int | None = None
    train_latency_p50_ms: int | None = None
    train_latency_mean_ms: int | None = None
    train_latency_max_ms: int | None = None

    val_hard: float | None = None
    val_soft: float | None = None
    val_activation_rate: float | None = None
    val_n_scored: int | None = None
    val_n_items: int | None = None
    val_n_agent_error: int | None = None
    val_n_judge_error: int | None = None
    val_latency_min_ms: int | None = None
    val_latency_p50_ms: int | None = None
    val_latency_mean_ms: int | None = None
    val_latency_max_ms: int | None = None

    # The second half of the hover card: what this step did to the skill.
    lines_added: int | None = None
    lines_removed: int | None = None
    files_touched: int | None = None
    # Training gold answers this step copied in verbatim, against its parent.
    # Zero on a well-behaved step; the overview raises it as a warning.
    n_answer_leaks: int | None = None
    # The agent's config version while this step ran; None if never probed.
    # Compared against the run's pinned version to spot a mid-run deploy.
    workspace_version: str | None = None
    n_edits_applied: int | None = None
    n_edits_skipped: int | None = None
    edit_summary: str | None = None
    skill_len: int | None = None
    candidate_from_cache: bool = False

    current_score: float | None = None
    best_score: float | None = None
    started_at: datetime
    completed_at: datetime | None = None


class OptimizationRunRename(BaseModel):
    """Body of PATCH /optimization/runs/{id} — the name, and nothing else.

    Same shape and same rules as an eval run's rename (`RunRename`): the two
    lists sit two clicks apart and rename the same way, so a name one accepts
    and the other refuses would be a difference with nothing behind it.
    """

    name: str | None = None

    @field_validator("name")
    @classmethod
    def _check(cls, value: str | None) -> str | None:
        return _clean_run_name(value)


class OptimizationRunOut(BaseModel):
    """One optimization run, as the left rail lists it."""

    id: uuid.UUID
    name: str | None
    created_by: str
    status: str
    mode: str
    skill_name: str
    num_epochs: int
    batch_size: int
    steps_per_epoch: int
    total_steps: int
    steps_done: int = 0
    best_step: int | None = None
    best_score: float | None = None
    cancel_requested: bool = False
    error_message: str | None = None
    # Why the loop ended — `status` cannot say, because every early stop is also
    # 'completed'. Null on runs that predate early stopping and on live ones.
    stop_reason: str | None = None
    started_at: datetime
    completed_at: datetime | None = None
    # Which eval sets this run drew questions from, for the list row's subtitle.
    source_eval_set_ids: list[uuid.UUID] = Field(default_factory=list)
    n_train: int = 0
    n_val: int = 0


class OptimizationRunDetail(OptimizationRunOut):
    """The overview page: the run, its settings, and every step of the chart."""

    # Non-secret settings only, by construction — `secrets` is a different column.
    config: dict = Field(default_factory=dict)
    detector: dict = Field(default_factory=dict)
    workspace_version: str | None = None
    steps: list[OptimizationStepSummary] = Field(default_factory=list)
    # Questions that landed in both splits. Not an error — the wizard offers it —
    # but validation is not held out for those, so the page says so.
    overlap_item_keys: list[str] = Field(default_factory=list)


class OptimizationRunPage(Page):
    items: list[OptimizationRunOut]


# --- The wizard -------------------------------------------------------------


class ImportPreviewRequest(BaseModel):
    """Which eval sets to draw questions from (wizard step 1)."""

    eval_set_ids: list[uuid.UUID] = Field(default_factory=list)


class PreviewQuestion(BaseModel):
    """One question the picker is offering, with what is known about it.

    `prior_accuracy` is `None` for a question nobody has run — never 0.0, which
    would read as "always wrong" and make it the first thing a developer reaches
    for. `prior_runs` is the denominator: 60% from five runs and 60% from one are
    different claims, and the questions most worth optimising are exactly the
    ones with the least history.
    """

    item_key: str
    question_id: str
    question: str
    ground_truth_response: str
    eval_set_id: uuid.UUID
    eval_set_name: str
    skills: list[str] = Field(default_factory=list)
    prior_accuracy: float | None = None
    prior_runs: int = 0


class SkillGroup(BaseModel):
    skill_name: str
    questions: list[PreviewQuestion] = Field(default_factory=list)


class PreviewSource(BaseModel):
    """One source set, with the fingerprint of the prompt it grades by.

    Two sets whose fingerprints differ were graded by different words, so their
    prior accuracies are not comparable — and the run about to be built will
    grade every question by a single prompt of its own. The wizard says so
    rather than letting the numbers imply otherwise.
    """

    id: uuid.UUID
    name: str
    n_questions: int
    judge_prompt_fingerprint: str


class ImportPreview(BaseModel):
    groups: list[SkillGroup] = Field(default_factory=list)
    # Questions with no skill tag, or with more than one. Shown, disabled, with
    # the tags they do carry — the fix is in the eval set, not here.
    ambiguous: list[PreviewQuestion] = Field(default_factory=list)
    sources: list[PreviewSource] = Field(default_factory=list)


class SkillCheck(BaseModel):
    """Whether the agent has the skill the questions are tagged with."""

    skill_name: str
    exists: bool
    files: list[str] = Field(default_factory=list)
    # The same paths, each with its own length. `n_chars` is their sum and is
    # kept because two callers already read it; this is what lets the wizard draw
    # the skill as a tree instead of as one number for the whole directory —
    # "4,820 characters" says nothing about which file holds them.
    file_chars: dict[str, int] = Field(default_factory=dict)
    n_chars: int = 0
    has_frontmatter: bool = False
    # Which agent server answered. The check used to read only the server's own
    # environment while the wizard collected a base URL of its own, so a
    # developer could clear a skill against one agent and run against another.
    agent_base_url: str = ""
    # Set when routing mode cannot be offered, with the reason to show instead.
    routing_blocked_reason: str | None = None
    available_skills: list[str] = Field(default_factory=list)
    workspace_version: str | None = None


class OptimizationConfig(BaseModel):
    """The non-secret settings one optimization run executes with.

    Every field is optional and blank means "use the environment", the same rule
    `RunConfig` follows — so the fake demo is runnable from an untouched form.

    The judge prompt is here rather than on an eval set, which is the opposite of
    how an eval run works, and deliberately. An eval run grades a set the whole
    team shares, so the criteria belong to the set. An optimization run draws
    from several sets at once — whose prompts may differ — and it is one
    developer's experiment, so the run owns one prompt and says which.
    """

    # Connections
    agent_base_url: str = ""
    agent_timeout_s: float | None = None
    langfuse_host: str = ""
    langfuse_public_key: str = ""
    langfuse_timeout_s: float | None = None
    llm_base_url: str = ""
    judge_model: str = ""
    optimizer_model: str = ""
    concurrency: int | None = Field(default=None, ge=1)

    # Grading
    judge_system_prompt: str = ""
    judge_user_prompt: str = ""
    judge_prompt_fingerprint: str = ""

    # The algorithm
    minibatch_size: int | None = Field(default=None, ge=1)
    learning_rate: int | None = Field(default=None, ge=1)
    min_learning_rate: int | None = Field(default=None, ge=1)
    scheduler: str = ""
    gate_metric: str = ""
    mixed_weight: float | None = Field(default=None, ge=0, le=1)
    # `None`, not `False`: a bool that defaults to False cannot say "the caller
    # did not mention this", so the deployment's own default could never apply —
    # every request would arrive asking for False. The wizard sends these only
    # once their control has been touched.
    failure_only: bool | None = None
    # Upstream's two longitudinal passes, both off by default. They run once per
    # epoch boundary, not per step, and both cost a call on the optimizer model.
    # `slow_update` writes guidance into a protected block of SKILL.md that
    # step-level analysts cannot edit; `meta_skill` is optimizer-side memory
    # shown to later analysts and never written into the skill at all.
    slow_update: bool | None = None
    meta_skill: bool | None = None
    analyst_workers: int | None = Field(default=None, ge=1)
    merge_batch_size: int | None = Field(default=None, ge=2)
    reflect_budget_chars: int | None = Field(default=None, ge=1000)
    seed: int | None = None

    # When the run stops before it has run out of steps (`optimizer/stopping.py`).
    # The two error settings replace the old single `error_threshold`, which
    # governed both splits at once and, past it, failed the whole run. A share
    # and a streak belong together: the share says what fraction of one split
    # may fail before its numbers are refused, the streak says how many refused
    # rollouts in a row are an agent server that has stopped answering.
    early_stop_train_error_share: float | None = Field(default=None, ge=0, le=1)
    early_stop_train_error_streak: int | None = Field(default=None, ge=0)
    early_stop_val_error_share: float | None = Field(default=None, ge=0, le=1)
    early_stop_val_error_streak: int | None = Field(default=None, ge=0)
    # 0 is off, and is the default: this one changes what a run produces rather
    # than protecting it from an outage.
    early_stop_patience: int | None = Field(default=None, ge=0)
    early_stop_target_score: float | None = Field(default=None, ge=0, le=1)


class OptimizationSecrets(BaseModel):
    """Write-only. No response model reads this — see `optimization_runs.secrets`."""

    llm_api_key: str = ""
    langfuse_secret_key: str = ""


class DetectorConfig(BaseModel):
    """How a run decides whether the agent actually used the skill.

    `path_patterns` are regexes matched against tool-call arguments; blank means
    the shipped default. `detectable` says the agent's traces are known to name
    skill file paths, which turns the content-matching fallback off.
    """

    path_patterns: list[str] = Field(default_factory=list)
    detectable: bool = False


class OptimizationRunCreate(BaseModel):
    name: str | None = None
    mode: str = "isolated"
    skill_name: str
    # `item_key`s, as the split editor produced them. A key may appear in both:
    # the wizard offers "also add to validation" deliberately.
    train: list[str] = Field(default_factory=list)
    val: list[str] = Field(default_factory=list)
    num_epochs: int = Field(default=1, ge=1, le=20)
    batch_size: int = Field(default=8, ge=1)
    config: OptimizationConfig = Field(default_factory=OptimizationConfig)
    secrets: OptimizationSecrets = Field(default_factory=OptimizationSecrets)
    detector: DetectorConfig = Field(default_factory=DetectorConfig)


# --- Optimize, Part 1: one rollout in detail --------------------------------


class OptimizationResultOut(BaseModel):
    """One question answered once, with the run's snapshot of what it asked.

    `question` and `ground_truth_response` come from `optimization_items`, not
    from `questions`: the run froze them at creation, and an eval set edited
    since would otherwise put today's text beside a six-week-old verdict.
    """

    id: uuid.UUID
    item_key: str
    question: str | None = None
    ground_truth_response: str | None = None
    correlation_id: str
    agent_response: str | None = None
    agent_latency_ms: int | None = None
    verdict: str | None = None
    judge_score: float | None = None
    judge_comment: str | None = None
    status: str  # pending | done | failed
    failure_kind: str | None = None
    error_message: str | None = None
    # NULL is a third answer, not a false: the detectors could not tell whether
    # the agent read the skill. Reporting it as "no" would make an unobservable
    # agent look like one that ignored its skill.
    activated: bool | None = None
    skills_read: list[str] | None = None
    detector_hit: str | None = None
    trace_ready: bool = False
    trace_error: str | None = None
    # Which analyst call this question was evidence for. NULL on validation,
    # which is never reflected on, and on training questions the reflect stage
    # did not use (a success, when the run is failure-only).
    minibatch_no: int | None = None


class OptimizationMinibatchOut(BaseModel):
    """One analyst call: the prompt sent, the patch proposed, what was cut.

    The prompt is stored rather than rebuilt because it is the evidence. It is
    also already truncated, which is what makes it safe to keep verbatim — its
    size is bounded by the reflect budget by construction.
    """

    minibatch_no: int
    source_type: str  # failure | success
    n_items: int
    item_keys: list[str] = Field(default_factory=list)
    prompt_system: str | None = None
    prompt_user: str | None = None
    raw_output: dict | None = None
    # [{item_key, span_index, field, before, after, stage}] — the cascade's
    # ledger. Shown in the UI, because a developer reading a proposal built on a
    # trace that lost 70% of its tool output should be told so on the page.
    truncation: list[dict] = Field(default_factory=list)
    chars_before: int | None = None
    chars_after: int | None = None
    error: str | None = None
    duration_ms: int | None = None


class OptimizationStageCallOut(BaseModel):
    """One merge or ranking call: what it was shown, and what it answered.

    Between the analysts and the applied skill there are two more model calls —
    a hierarchical merge and, if the pool overflows the learning rate, a ranking
    — and either can drop an edit. Steps recorded before this existed have none
    of these, and the page says so rather than implying there were none.
    """

    seq: int
    stage: str  # merge_failure | merge_success | merge_final | ranking
    # Which round of the hierarchical merge; null where the stage runs once.
    level: int | None = None
    prompt_system: str | None = None
    prompt_user: str | None = None
    # Parsed, not raw. Every one of these stages is a JSON contract, and a reply
    # that could not be parsed had its patch discarded — which `error` says.
    output: dict | None = None
    error: str | None = None
    duration_ms: int | None = None


class OptimizationRolloutDetail(BaseModel):
    """Part 1: one step, one split — the numbers, the questions, the analysts."""

    run_id: uuid.UUID
    step_no: int
    split: str
    epoch_no: int
    step_in_epoch: int
    # Which skill version was rolled out. On training this is the parent step's
    # accepted skill, not this step's candidate — the header has to say so or
    # the two rollouts of a step look like they measured the same thing.
    skill_step_no: int
    parent_step_no: int | None = None
    step_status: str
    gate_action: str | None = None
    gate_reject_reason: str | None = None
    edit_summary: str | None = None

    n_items: int = 0
    n_scored: int = 0
    n_agent_error: int = 0
    n_judge_error: int = 0
    hard: float | None = None
    soft: float | None = None
    activation_rate: float | None = None
    n_activated: int = 0
    latency_min_ms: int | None = None
    latency_p50_ms: int | None = None
    latency_mean_ms: int | None = None
    latency_max_ms: int | None = None
    aborted: bool = False
    abort_reason: str | None = None

    # What became of the edits the analysts proposed. Carried on the *training*
    # page because that is where the proposals are read: the minibatch pane shows
    # what was asked for, and without this the reader had to leave the page to
    # find out whether any of it landed.
    n_edits_applied: int | None = None
    n_edits_skipped: int | None = None
    edit_reports: list["EditReportOut"] = Field(default_factory=list)
    # Whether this step bought a validation rollout at all. False when every edit
    # was refused: the candidate is then identical to a skill already scored, so
    # the engine reuses that score instead. The page has to say so — a validation
    # tab that simply 404s reads as a bug.
    val_rolled_out: bool = True

    results: list[OptimizationResultOut] = Field(default_factory=list)
    # Empty on validation, which is measured and never reflected on.
    minibatches: list[OptimizationMinibatchOut] = Field(default_factory=list)
    # The step's own stages, after the per-minibatch analysts: merge, then rank.
    # Also empty on validation, and on any step run before they were recorded.
    stage_calls: list[OptimizationStageCallOut] = Field(default_factory=list)


class SkillDiffFile(BaseModel):
    """One file's two sides, for a diff the browser lays out itself.

    `before` and `after` are `None` — not `""` — when the file did not exist on
    that side. A created file and an emptied one produce the same line counts,
    and only this distinguishes them; the tree labels one "new" and the other
    "removed", and a reader deciding whether the agent can still reach a
    reference document needs the difference.
    """

    path: str
    before: str | None = None
    after: str | None = None
    # From `skillio.per_file_stats`, never recounted downstream: two answers to
    # "how many lines changed" eventually disagree on screen about one edit.
    added: int = 0
    removed: int = 0


class AnswerLeak(BaseModel):
    """A gold answer this step copied verbatim into the skill."""

    path: str
    answer: str
    line: str


class EditReportOut(BaseModel):
    """What became of one proposed edit.

    Every field has a default because this comes from the vendored apply stage:
    a shape change upstream should show up as a thinner row on the page, not as
    a 500 on a run that has already finished.
    """

    index: int | None = None
    op: str = ""
    path: str = ""
    path_defaulted: bool = False
    target: str = ""
    content_preview: str = ""
    status: str = ""
    error: str | None = None


class OptimizationSkillDiff(BaseModel):
    """Part 2: one step's edits, against the snapshot they were derived from."""

    run_id: uuid.UUID
    skill_name: str
    mode: str
    step_no: int
    # What was asked for, and what it resolved to. `parent` is the last step the
    # gate *accepted* — usually not `step_no - 1`, because a rejected step rolls
    # the skill back — and it is NULL until the first acceptance, which is what
    # `base_is_fallback` reports rather than hiding.
    base: str
    base_step_no: int
    base_is_fallback: bool = False

    gate_action: str | None = None
    gate_reject_reason: str | None = None
    is_best: bool = False
    step_status: str
    edit_summary: str | None = None
    n_edits_applied: int | None = None
    n_edits_skipped: int | None = None

    files: list[SkillDiffFile] = Field(default_factory=list)
    # Named, not carried. The tree is a picture of the whole skill, but sending
    # every file's text on every request grows the payload with the skill rather
    # than with the edit.
    unchanged_paths: list[str] = Field(default_factory=list)
    # The same files, with their contents. `unchanged_paths` names them for the
    # tree; this lets the pane draw a real all-context diff for one, so a step
    # that changed nothing still looks like a diff rather than like a sentence
    # where the diff used to be.
    unchanged_files: list["SkillDiffFile"] = Field(default_factory=list)
    lines_added: int = 0
    lines_removed: int = 0
    answer_leaks: list[AnswerLeak] = Field(default_factory=list)
    edit_reports: list[EditReportOut] = Field(default_factory=list)
