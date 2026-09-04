"""What a developer may keep as their own default, and — just as important —
what they may not.

Every form in this product opens on values the deployment chose: the agent
server in `AGENT_CHAT_URL`, the grading model in `JUDGE_MODEL`, the batch size in
`OPTIMIZER_BATCH_SIZE`. That is right for a deployment and wrong for a person.
Someone who points every run at their own agent server retypes the same address
a dozen times a day, and the "Run eval" dialog has no memory of yesterday. This
catalogue is the list of things they can set once.

**A key belongs here when two things are true.** The deployment configures it
through an environment variable, *and* some form in the browser already lets a
developer override it for one run. Both halves matter and neither implies the
other:

  * env but no control — `OPTIMIZER_SCHEDULER` is real and settable through the
    API, but nothing on any screen offers it. A preference for a control that
    does not exist is a preference nobody can see working.
  * control but no env — the wizard's "Trajectory budget" is a literal in
    `reflection.py`. Offering a user default for it would be inventing a
    deployment setting through the back door.

**The list of exclusions is as load-bearing as the list of entries.** Some keys
are absent because nobody has got to them; others are absent because putting
them here would be a mistake — `SCRIPT_MAX_QUERIES` and its neighbours are
containment boundaries, not preferences, and a user who can raise their own
sandbox limit does not have one. A bare set of names cannot tell those two
apart six months from now, so every exclusion carries a written reason and a
test refuses a blank one.

**Adding a setting.** Put a `SettingSpec` in `CATALOG`. If it does not belong,
put its name in `EXCLUDED_SETTINGS` (for a field on `Settings`) or
`EXCLUDED_KEYS` (for a key in one of the prefill dictionaries) with the reason.
Then regenerate the browser's copy:

    python -m app.settings_catalog > ../frontend/src/settings_catalog.json

`backend/tests/test_settings_catalog.py` and
`frontend/src/settings_catalog.test.js` check both halves and both directions,
so forgetting any of this is a red test rather than a settings page that has
quietly stopped covering the thing people retype every day.

Note what is *not* here: this module knows which keys exist and what a legal
value is. It does not know what any user chose — that is
`app/services/user_settings.py`, and the separation is deliberate. See that
module's siblings in `tests/test_user_settings_isolation.py` for why the overlay
must never reach the path a run actually executes through.
"""
from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from typing import Any

from app.config import settings

# Kinds, and what each one means for storage:
#
#   text      a string; "" is a legal stored value meaning "deliberately blank"
#   int       whole number
#   float     number
#   bool      True/False; the settings page gives it three positions, because
#             "follow the deployment" is a third state a checkbox cannot hold
#   fraction  stored 0..1, typed as a whole percent — the wizard's `scale: 100`
#   secret    never returned, never in `values`; see services/user_secrets.py
KINDS = ("text", "int", "float", "bool", "fraction", "secret")


@dataclass(frozen=True)
class SettingSpec:
    """One settable key: where its system value comes from, and what is legal."""

    key: str
    """The name the forms and the run config use — `concurrency`, not
    `run_concurrency`. The two differ often enough that guessing one from the
    other is not safe."""

    setting: str
    """The attribute on `Settings`. Its environment variable is this uppercased,
    which is how the `.env.example` cross-check finds it."""

    group: str
    kind: str
    label: str
    help: str = ""
    minimum: float | None = None
    maximum: float | None = None
    optional: bool = False
    """`None` is a legal value meaning "off". Only `early_stop_target_score`:
    there is no number that stands for "aim at nothing"."""

    endpoint_key: str | None = None
    """Secrets only: the key holding the URL this credential authenticates
    against. A stored credential is only ever sent to that endpoint."""

    def replace_key(self, key: str) -> "SettingSpec":
        """A copy under a different name. Only tests need this — it is how they
        simulate a key being added to a later release."""
        return dataclasses.replace(self, key=key)


GROUPS = (
    ("agent", "Agent", "The service that answers each question."),
    ("speed", "Speed", "How hard a run pushes the agent server."),
    ("trace", "Trace store", "Where the agent records what it did, step by step."),
    ("models", "Models", "The models that grade answers and explain the wrong ones."),
    ("optimization", "Optimization", "What a training run does by default."),
    ("early_stopping", "Early stopping", "When a training run gives up."),
)


CATALOG: tuple[SettingSpec, ...] = (
    # --- Agent --------------------------------------------------------------
    SettingSpec(
        key="agent_chat_url", setting="agent_chat_url", group="agent", kind="text",
        label="Chat endpoint",
        help=(
            "The full URL of your agent's OpenAI chat completions endpoint — "
            "questions are sent here. Everything else on this page is optional; "
            "this is not."
        ),
    ),
    # Optional, and the help text has to earn the extra field: someone who
    # leaves it blank gets a working evaluation and no explanation of what they
    # gave up. Naming what it unlocks is the only thing on this page that turns
    # a blank box into a decision.
    SettingSpec(
        key="agent_skills_url", setting="agent_skills_url", group="agent", kind="text",
        label="Skills endpoint",
        optional=True,
        help=(
            "Optional. The full URL that lists your agent's skill files. Without "
            "it evaluation still runs; the playground, the skill-coverage warning "
            "and optimization need it."
        ),
    ),
    # Both optional, and inert together: with no key nothing is sent and the
    # request is byte for byte what it was before authentication existed. They
    # are here because a team whose agent sits behind a gateway could not
    # connect at all, and the deployment's own AGENT_API_KEY is the wrong place
    # for a credential that belongs to one person.
    SettingSpec(
        key="agent_api_key", setting="agent_api_key", group="agent", kind="secret",
        label="Agent API key", endpoint_key="agent_chat_url", optional=True,
        help=(
            "Optional — most agent servers need none. Only ever sent to the chat "
            "endpoint above, and to the skills endpoint when that is the same "
            "server. Change the chat endpoint and this must be entered again."
        ),
    ),
    SettingSpec(
        key="agent_auth_header", setting="agent_auth_header", group="agent", kind="text",
        label="Auth header", optional=True,
        help=(
            "Optional. Blank sends the key as `Authorization: Bearer <key>`. Name "
            "a header instead — `X-Api-Key` — and the key is sent as that "
            "header's value, with no prefix."
        ),
    ),
    SettingSpec(
        key="agent_timeout_s", setting="agent_timeout_s", group="agent", kind="float",
        label="Agent timeout", help="Seconds one question may take before it counts as failed.",
        minimum=1,
    ),
    # --- Speed ---------------------------------------------------------------
    SettingSpec(
        key="concurrency", setting="run_concurrency", group="speed", kind="int",
        label="Concurrency", help="How many questions are sent to the agent at once.",
        # 32 is the Optimize wizard's ceiling. The eval run schema has none, but a
        # default of 64 stored here would be rejected by a form the user never
        # touched — so the strictest consumer decides. See the bounds test.
        minimum=1, maximum=32,
    ),
    # --- Trace store ---------------------------------------------------------
    SettingSpec(
        key="langfuse_host", setting="langfuse_host", group="trace", kind="text",
        label="Langfuse host",
    ),
    SettingSpec(
        key="langfuse_public_key", setting="langfuse_public_key", group="trace", kind="text",
        label="Langfuse public key",
    ),
    SettingSpec(
        key="langfuse_secret_key", setting="langfuse_secret_key", group="trace", kind="secret",
        label="Langfuse secret key", endpoint_key="langfuse_host",
        help="Only ever sent to the host above. Change the host and this must be entered again.",
    ),
    SettingSpec(
        key="langfuse_timeout_s", setting="langfuse_timeout_s", group="trace", kind="float",
        label="Langfuse timeout", help="Seconds.", minimum=1,
    ),
    # --- Models --------------------------------------------------------------
    SettingSpec(
        key="llm_base_url", setting="llm_base_url", group="models", kind="text",
        label="LLM base URL", help="Used by the judge, the diagnosis and the optimizer.",
    ),
    SettingSpec(
        key="llm_api_key", setting="llm_api_key", group="models", kind="secret",
        label="LLM API key", endpoint_key="llm_base_url",
        help="Only ever sent to the base URL above. Change the URL and this must be entered again.",
    ),
    SettingSpec(
        key="judge_model", setting="judge_model", group="models", kind="text",
        label="Grading model",
    ),
    SettingSpec(
        key="diagnosis_model", setting="diagnosis_model", group="models", kind="text",
        label="Diagnosis model",
    ),
    SettingSpec(
        key="diagnosis_enabled", setting="diagnosis_enabled", group="models", kind="bool",
        label="Diagnose wrong answers",
        help="One extra model call per wrong answer. Off still allows diagnosing one question by hand.",
    ),
    # --- Optimization --------------------------------------------------------
    SettingSpec(
        key="optimizer_model", setting="optimizer_model", group="optimization", kind="text",
        label="Optimizer model",
        help="The one call that reads a whole minibatch of traces at once.",
    ),
    SettingSpec(
        key="num_epochs", setting="optimizer_num_epochs", group="optimization", kind="int",
        label="Epochs", help="Passes over the training split.", minimum=1, maximum=20,
    ),
    SettingSpec(
        key="batch_size", setting="optimizer_batch_size", group="optimization", kind="int",
        label="Batch size", help="Questions per step.", minimum=1,
    ),
    SettingSpec(
        key="learning_rate", setting="optimizer_learning_rate", group="optimization", kind="int",
        label="Learning rate", help="The most edits one step may apply.", minimum=1,
    ),
    SettingSpec(
        key="minibatch_size", setting="optimizer_minibatch_size", group="optimization", kind="int",
        label="Analyst batch size",
        help="Trajectories per analyst call. This is what decides whether the call fits in the model's context window.",
        minimum=1,
    ),
    SettingSpec(
        key="gate_metric", setting="optimizer_gate_metric", group="optimization", kind="text",
        label="Gate score",
        help=(
            "Which number decides whether a step's edit is kept: 'hard' counts a "
            "question only when it is completely right, 'soft' gives partial "
            "credit, 'mixed' weighs the two. Routing runs usually want soft or "
            "mixed — a strict set match over a few dozen questions can sit flat."
        ),
    ),
    SettingSpec(
        key="mixed_weight", setting="optimizer_mixed_weight", group="optimization",
        kind="float", label="Weight on partial credit",
        help="Only used when the gate score is 'mixed'. 0 is hard alone, 1 is soft alone.",
        minimum=0, maximum=1,
    ),
    SettingSpec(
        key="slow_update", setting="optimizer_slow_update", group="optimization", kind="bool",
        label="Slow update", help="Once per epoch, write guidance into a protected block of the skill.",
    ),
    SettingSpec(
        key="meta_skill", setting="optimizer_meta_skill", group="optimization", kind="bool",
        label="Meta skill", help="Optimizer-side memory shown to later analysts.",
    ),
    # --- Early stopping ------------------------------------------------------
    SettingSpec(
        key="early_stop_train_error_share", setting="early_stop_train_error_share",
        group="early_stopping", kind="fraction", label="Training errors tolerated",
        help="Share of a training batch that may fail before its numbers are refused.",
        minimum=0, maximum=1,
    ),
    SettingSpec(
        key="early_stop_train_error_streak", setting="early_stop_train_error_streak",
        group="early_stopping", kind="int", label="Training error streak",
        help="Refused training batches in a row before the run stops. 0 is off.",
        minimum=0,
    ),
    SettingSpec(
        key="early_stop_val_error_share", setting="early_stop_val_error_share",
        group="early_stopping", kind="fraction", label="Validation errors tolerated",
        minimum=0, maximum=1,
    ),
    SettingSpec(
        key="early_stop_val_error_streak", setting="early_stop_val_error_streak",
        group="early_stopping", kind="int", label="Validation error streak",
        help="0 is off.", minimum=0,
    ),
    SettingSpec(
        key="early_stop_patience", setting="early_stop_patience",
        group="early_stopping", kind="int", label="Steps without a new best",
        help="0 is off.", minimum=0,
    ),
    SettingSpec(
        key="early_stop_target_score", setting="early_stop_target_score",
        group="early_stopping", kind="fraction", label="Good enough",
        help="Stop as soon as validation reaches this.",
        minimum=0, maximum=1, optional=True,
    ),
)

BY_KEY: dict[str, SettingSpec] = {spec.key: spec for spec in CATALOG}

SECRET_KEYS: tuple[str, ...] = tuple(s.key for s in CATALOG if s.kind == "secret")

# The one home for "which URL does this credential authenticate against".
# `routers/runs.py` used to carry its own copy for borrowed run credentials.
SECRET_ENDPOINTS: dict[str, str] = {
    s.key: s.endpoint_key for s in CATALOG if s.kind == "secret"
}


# --- What is deliberately not offered ---------------------------------------
#
# Fields on `Settings`, by attribute name. A new one that appears in neither this
# mapping nor `CATALOG` fails `test_settings_catalog.py`, which is the whole
# point: the decision gets made once, in writing, by whoever adds the variable.

EXCLUDED_SETTINGS: dict[str, str] = {
    # Infrastructure the app is served on. Not a preference in any sense, and
    # several of them cannot change without a restart.
    "database_url": "infrastructure — one database per deployment",
    "sync_database_url": "infrastructure — one database per deployment",
    "db_pool_size": "process-wide resource budget, sized against one uvicorn worker",
    "db_max_overflow": "process-wide resource budget",
    "db_pool_timeout_s": "process-wide resource budget",
    "db_pool_recycle_s": "process-wide resource budget",
    "db_pool_pre_ping": "process-wide resource budget",
    "root_path": "set by the reverse proxy in front of this process",
    "docs_dir": (
        "where this deployment keeps the reference markdown on disk. A "
        "packaging detail, and pointing it somewhere else changes nothing a "
        "developer would recognise as a preference"
    ),
    "frontend_origin": "CORS. A user-settable origin is a user-settable security boundary",
    "log_level": "operational, and shared by every request in the process",
    # Identity. Who the caller is, and how that is decided, is emphatically not
    # something the caller configures.
    "auth_mode": "decides how identity is established; a user cannot choose their own",
    "fake_user_subject": "development identity, chosen by the deployment",
    "known_users": "the demo directory, chosen by the deployment",
    "keycloak_url": "identity provider, one per deployment",
    "keycloak_realm": "identity provider, one per deployment",
    "keycloak_client_id": "identity provider, one per deployment",
    "keycloak_audience": "identity provider, one per deployment",
    "keycloak_jwks_cache_s": "identity provider, one per deployment",
    "hr_api_base_url": "the employee directory the share picker resolves against",
    "hr_api_verify_ssl": "TLS verification is not a per-user preference",
    "hr_api_timeout_s": "shared directory lookup, tuned once",
    "settings_secret_key": "the key this feature's own credentials are encrypted with",
    # Which seams are real. A user cannot decide that the agent is no longer
    # simulated; the deployment can.
    "agent_impl": "seam selection belongs to the deployment",
    "judge_impl": "seam selection belongs to the deployment",
    "trace_impl": "seam selection belongs to the deployment",
    "diagnosis_impl": "seam selection belongs to the deployment",
    "synthesis_impl": "seam selection belongs to the deployment",
    "optimizer_impl": "seam selection belongs to the deployment",
    "workspace_impl": "seam selection belongs to the deployment",
    # Real settings with no control on any form. These are the ones to revisit:
    # the day one of them gets a field, it belongs in CATALOG.
    "agent_max_retries": "no control on any form yet",
    "agent_probe_timeout_s": "no control on any form yet — the dialog's pre-flight budget",
    "llm_timeout_s": "no control on any form yet",
    "llm_max_retries": "no control on any form yet",
    "synthesis_model": "no control on any form yet",
    "judge_score_threshold": "no control on any form yet, and it changes what a verdict means",
    "langfuse_observation_types": "no control on any form yet",
    "langfuse_trace_read_strategy": "no control on any form yet",
    "trace_poll_backoff_s": "no control on any form yet",
    "trace_poll_max_attempts": "no control on any form yet",
    "trace_settle_delay_s": "no control on any form yet",
    "trace_settle_max_reads": "no control on any form yet",
    "optimizer_min_learning_rate": "no control on any form yet",
    "optimizer_analyst_workers": "no control on any form yet",
    "optimizer_merge_batch_size": "no control on any form yet",
    "optimizer_scheduler": "no control on any form yet",
    "optimizer_failure_only": "no control on any form yet",
    # Containment boundaries. Every one of these bounds what one request may do
    # to the process; a user who can raise their own limit does not have one.
    "script_max_rows_per_query": "containment boundary, not a preference",
    "script_statement_timeout_s": "containment boundary, not a preference",
    "script_wall_clock_s": "containment boundary — and nginx has to agree with it",
    "script_max_queries": "containment boundary, not a preference",
    "script_max_output_chars": "containment boundary, not a preference",
    "script_memory_mb": "containment boundary, not a preference",
    "script_max_concurrent_runs": "containment boundary — the process has one worker",
    "playground_max_attempts_per_user": "bounds this process's memory, one trace per attempt",
    "sse_queue_max_events": "bounds one subscriber's mailbox in this process",
    "span_body_max_chars": "bounds what one diagnosis prompt may carry",
    "error_message_max_chars": "bounds one stored error message",
    "export_trace_concurrency": "bounds what one download does to the trace store",
    "export_max_traces": "bounds what one download does to the trace store",
}

# Keys in the prefill dictionaries (`run_config.defaults()`,
# `hyperparams.algorithm_defaults()`, `StopPolicy.as_dict()`) that are not
# offered. Same rule, different namespace: these are wire names, not `Settings`
# attributes.
EXCLUDED_KEYS: dict[str, str] = {
    "scheduler": "no control on any form yet",
    "min_learning_rate": "no control on any form yet",
    "analyst_workers": "no control on any form yet",
    "merge_batch_size": "no control on any form yet",
    "gate_metric": "no control on any form yet",
    "mixed_weight": "no control on any form yet",
    "failure_only": "no control on any form yet",
    "reflect_budget_chars": (
        "on the wizard but not in the environment — its default is "
        "reflection.py's DEFAULT_REFLECT_BUDGET_CHARS. A user default is only "
        "offered where a deployment can already configure one."
    ),
}


# --- Reading the environment through the catalogue --------------------------

def system_value(spec: SettingSpec) -> Any:
    """This deployment's value for one setting."""
    return getattr(settings, spec.setting)


def system_defaults() -> dict[str, Any]:
    """Every non-secret setting's current environment value, by wire name.

    Secrets are absent by construction rather than by filtering at the call
    site: this dictionary is rendered into a page.
    """
    return {
        spec.key: system_value(spec) for spec in CATALOG if spec.kind != "secret"
    }


def as_json() -> list[dict[str, Any]]:
    """The catalogue as the browser reads it.

    Values are not included — the shape is public, the deployment's settings are
    fetched per request, and the credentials are never sent at all.
    """
    return [
        {
            "key": spec.key,
            "group": spec.group,
            "kind": spec.kind,
            "label": spec.label,
            "help": spec.help,
            "minimum": spec.minimum,
            "maximum": spec.maximum,
            "optional": spec.optional,
            "endpoint_key": spec.endpoint_key,
        }
        for spec in CATALOG
    ]


if __name__ == "__main__":  # pragma: no cover - generator, not app code
    print(json.dumps(as_json(), indent=2))
