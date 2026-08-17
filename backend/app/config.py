"""Application settings (non-latency).

All *fake-layer* latency knobs live in `app/fake_config.py` — the single file
required by TASK.md. This module holds everything else: DB URLs, the fake-login
switch, CORS, the §6.7 span-body truncation limit, and the configuration for the
real integrations (HTTP agent, OpenAI-compatible LLM, Langfuse).

Note the trace-poll knobs live *here*, not in fake_config: they govern the wait
for real Langfuse ingestion too, and real ingestion lags by orders of magnitude
more than the fake layer's simulated one.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Impl = Literal["fake", "real"]
# Same fake/real shape as the six seams above, for the one dependency that is not
# an outbound integration: who the caller is.
AuthMode = Literal["fake", "keycloak"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Async URL for the app; sync URL for Alembic migrations.
    database_url: str = "postgresql+asyncpg://agentopt:agentopt@localhost:5432/agentopt"
    sync_database_url: str = "postgresql+psycopg://agentopt:agentopt@localhost:5432/agentopt"

    # --- Database connection pool ------------------------------------------
    # SQLAlchemy's defaults are 5 + 10 overflow. With a single uvicorn worker
    # (docker-compose.prod.yml, deliberately) that is the entire backend's
    # concurrency budget, and it was sized for the seeded demo rather than for a
    # room full of people.
    #
    # `pool_size + max_overflow` is per worker. There is exactly one worker, and
    # that is a constraint rather than a default — the SSE hub, the playground's
    # attempt store, `cancellation`, and the startup run reaper all assume it
    # (see docker-compose.prod.yml). So 20 + 10 is a single process's ceiling,
    # comfortably under Postgres' `max_connections` (100, not overridden by the
    # compose file) with room for migrations and a psql session. If the
    # single-worker constraint is ever lifted, this has to be divided by the
    # worker count, not carried over.
    db_pool_size: int = 20
    db_max_overflow: int = 10
    # SQLAlchemy's default is 30s. Waiting half a minute and *then* failing reads
    # to the user as a frozen page; failing sooner at least lets the UI say
    # something. Once nothing holds a connection across an external call, a
    # healthy backend never queues here at all.
    db_pool_timeout_s: float = 10.0
    # A proxy or firewall between here and Postgres will drop idle connections
    # without telling either end; recycling ahead of that turns a mysterious
    # first-request failure into nothing at all.
    db_pool_recycle_s: int = 1800
    # One lightweight round trip per checkout, in exchange for surviving a
    # database restart without a burst of ConnectionDoesNotExistError.
    db_pool_pre_ping: bool = True

    # --- Identity -----------------------------------------------------------
    # fake     -> trust the X-User-Subject header (local dev, the seeded demo,
    #             and the owner/viewer switch in the top bar).
    # keycloak -> verify a Keycloak-issued bearer token and take the subject from
    #             its `preferred_username` claim.
    #
    # Only `current_subject` in app/auth.py branches on this. Every role check
    # below it (role_for / require_reader / require_owner) takes a subject string
    # and is identical in both modes — which is what makes the swap this small.
    auth_mode: AuthMode = "fake"

    # Fake logged-in user (§6.16). Role is NOT stored here — it is resolved per
    # eval_set from the `eval_set_roles` table using this subject.
    fake_user_subject: str = "alice"

    # Fake user directory: the selectable identities for the login switch and the
    # share pickers. In keycloak mode the directory is the org's, reached through
    # the employee lookup below.
    known_users: list[str] = ["alice", "bob", "carol", "dave"]

    # --- Keycloak (auth_mode="keycloak") ------------------------------------
    # Base URL *including* any relative path the deployment is served under —
    # Keycloak dropped the historical /auth prefix in 17, but a deployment can
    # (and ours does) put it back, so this is copied verbatim rather than
    # assembled. Example: https://keycloak.example.com/auth
    keycloak_url: str = ""
    keycloak_realm: str = "tsmc"
    keycloak_client_id: str = "ai4bi-public"
    # Expected `aud` claim. Blank disables the audience check entirely.
    #
    # It is a separate setting from client_id on purpose: Keycloak only puts the
    # client id in `aud` when an audience mapper says so, and otherwise emits
    # something else ("account" is the common default). Guessing wrong makes
    # *every* token fail, so `verify_token` reports the value it actually saw and
    # this knob is how you act on that without a code change.
    keycloak_audience: str = "ai4bi-public"
    # How long the signing keys are cached. An unknown `kid` forces a refetch
    # regardless, so this only bounds how long a *revoked* key stays trusted.
    keycloak_jwks_cache_s: int = 3600

    # --- Employee directory lookup (share picker) ---------------------------
    # Resolves a typed username to a real person before it is written into
    # eval_set_roles. Without it a typo shares an eval set with nobody, silently.
    # The username in the path is the same string as the token's
    # preferred_username.
    hr_api_base_url: str = "https://cpochatproxyservice.cpoap-dev.dev.tsmc.com/proxy/employees"
    # The internal service presents a self-signed certificate. Flip this on once
    # the corporate CA bundle is installed in the backend image.
    hr_api_verify_ssl: bool = False
    # Deliberately short: this call sits in front of a keystroke, and a directory
    # that is merely slow must not make the share dialog feel broken.
    hr_api_timeout_s: float = 5.0

    # Prefix this app is mounted under by a reverse proxy that strips it before
    # forwarding (nginx `proxy_pass …:8000/` under `location /api/`). It does not
    # change the routes; it makes the generated /docs and /openapi.json URLs
    # carry the prefix, which is otherwise the one thing that breaks behind a
    # stripping proxy.
    root_path: str = ""

    frontend_origin: str = "http://localhost:5173"

    # §6.7: only a single span's over-long input/output body is truncated (head+tail
    # kept, middle elided). The span skeleton is never dropped.
    span_body_max_chars: int = 800

    # --- Integration selection (§9.2) --------------------------------------
    # One switch per seam, so the real integrations can be brought up one at a
    # time (real agent while the judge is still fake, and so on). Default stays
    # "fake" so the seeded demo and `SEED=1 ./scripts/dev.sh` behave as before.
    agent_impl: Impl = "fake"
    judge_impl: Impl = "fake"
    trace_impl: Impl = "fake"
    diagnosis_impl: Impl = "fake"
    # Drafts an expected reasoning process from a trace, for a question being
    # promoted out of the playground (§10.8). Shares the LLM endpoint with the
    # judge and the diagnosis.
    synthesis_impl: Impl = "fake"
    # The model that turns scored rollouts into skill edits — SkillOpt's
    # "optimizer" role (reflect, aggregate, rank/select). Its own seam because it
    # is the one call in the loop that is neither the agent nor the judge, and
    # because without a fake for it the Optimize section could not be
    # demonstrated on Docker alone, which is the property the whole fake layer
    # exists to preserve.
    optimizer_impl: Impl = "fake"

    # The playground's view of the agent's config + skill files (§10.2).
    # Read-only against the agent server, so it is the cheapest seam to switch
    # on first.
    workspace_impl: Impl = "fake"

    # --- Agent HTTP server (§6.2) -------------------------------------------
    # Base URL of the FastAPI agent server; the client POSTs to {base}/execute.
    agent_base_url: str = ""
    agent_timeout_s: float = 120.0
    agent_max_retries: int = 2

    # --- LLM (OpenAI-compatible endpoint; judge + diagnosis) ---------------
    llm_base_url: str = "http://litellm-ai4bi.cpoap-dev.dev.tsmc.com"
    llm_api_key: str = ""
    llm_timeout_s: float = 120.0
    llm_max_retries: int = 2
    judge_model: str = "Qwen3.6-27B"
    diagnosis_model: str = "Qwen3.6-27B"
    # Deliberately the same default as the other two, and deliberately separate:
    # this is the call that has to reason about a whole minibatch of traces at
    # once, so it is the one people will want to point at a stronger model.
    optimizer_model: str = "Qwen3.6-27B"
    synthesis_model: str = "Qwen3.6-27B"
    # Optional override: when set, the verdict is derived from the judge's
    # continuous score (score >= threshold -> correct) instead of trusting the
    # verdict field the model returned. §6.7 deliberately left this knob open.
    judge_score_threshold: float | None = None

    # --- Langfuse (trace store; read-only in Stage 1) ----------------------
    langfuse_host: str = "http://langfuse-ai4bi.cpoap-dev.dev.tsmc.com"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_timeout_s: float = 60.0
    # Observation types rebuilt into the span list. EVENT observations carry no
    # input/output worth showing, so they are dropped by default.
    langfuse_observation_types: list[str] = ["GENERATION", "SPAN"]
    # Which of Langfuse's read APIs to pull a trace's observations from.
    #
    #   auto              try each strategy in turn, first success wins (default)
    #   trace_api         GET /api/public/traces/{id} only
    #   observations_api  GET /api/public/v2/observations?traceId= only
    #
    # `auto` exists because these two endpoints are served by different queries
    # inside Langfuse, and some self-hosted builds can serve one but not the
    # other — see the `events` table note in real/langfuse.py. Pin a single
    # strategy once a deployment is known-good to skip the wasted first call.
    langfuse_trace_read_strategy: str = "auto"

    # --- Run execution -----------------------------------------------------
    # 1 keeps the original strictly-sequential behaviour. Raise it to run
    # questions against the agent concurrently.
    run_concurrency: int = 1
    # Trace-ready polling (§6.12), used by the orchestrator and the view path.
    # The list is consumed in order; the last value repeats if more polls are
    # needed. Sized for real Langfuse ingestion, which can lag tens of seconds.
    trace_poll_backoff_s: list[float] = [0.5, 1.0, 2.0, 4.0, 8.0]
    # Safety cap so a never-ready trace can't stall a run forever.
    trace_poll_max_attempts: int = 8
    # Settling a trace that has *started* arriving (§6.12a).
    #
    # Langfuse ingestion is not only late, it is incremental: the first read that
    # returns any observation at all can be a trace that is still filling up. The
    # span most often missing is the last one — the agent's final answer
    # generation, which ends microseconds before the HTTP response that makes
    # this platform go looking for the trace. So the first non-empty read is
    # re-read until the span count stops growing, rather than trusted outright.
    #
    # `0` reads restores the old take-the-first-read behaviour. The cost when
    # nothing is pending is one extra request and one delay per question.
    trace_settle_delay_s: float = 1.0
    trace_settle_max_reads: int = 3
    # How much of an exception message is kept in question_results.error_message.
    error_message_max_chars: int = 2000

    # --- Live progress streams ----------------------------------------------
    # How many events one subscriber's mailbox holds before the oldest are
    # dropped and the stream tells the client to resync (see app/sse.py). The
    # bound exists because a subscriber that stops reading — a stalled
    # connection, a sleeping laptop — would otherwise grow a queue for as long as
    # its stream is open, and the playground's per-user stream stays open for as
    # long as the tab does. 512 is far more than any healthy client is ever
    # behind by; reaching it means the connection is already broken.
    sse_queue_max_events: int = 512

    # --- Playground (§10) ---------------------------------------------------
    # Playground attempts are deliberately not persisted, so they live in this
    # process's memory. The cap is not decoration: one attempt holds a whole
    # trace, which for a real agent is hundreds of KB of span bodies (§9.19), so
    # an unbounded store would leak the process's memory one attempt at a time.
    # Oldest attempts are evicted first, per subject.
    playground_max_attempts_per_user: int = 20

    # --- Export -------------------------------------------------------------
    # Traces are the only part of an export that leaves the database: each one
    # is a live read against the trace store, so a 600-question export is 600
    # round trips. Fetched concurrently, and capped so a whole run history
    # cannot turn one download into a multi-minute request.
    export_trace_concurrency: int = 8
    export_max_traces: int = 1000

    # --- Logging -------------------------------------------------------------
    # Applied to the root logger at startup (app/main.py). INFO because the
    # eval-set script audit trail is written at that level and is meant to be on
    # by default — see the comment there.
    log_level: str = "INFO"

    # --- Eval sets built by running an uploaded Python script ----------------
    # Every number here is a containment boundary rather than a preference, so
    # each is settable per deployment but none is meant to be raised casually.
    # See app/services/script_runner.py for what each one prevents.
    #
    # Per query. Breaching it raises *into the script* rather than truncating:
    # an eval set computed from half the rows looks fine and is wrong.
    script_max_rows_per_query: int = 50_000
    # Enforced by the target database via `statement_timeout`, so it holds even
    # when this process is busy.
    script_statement_timeout_s: int = 600
    # The whole run. A sleeping script burns no CPU, so this — not RLIMIT_CPU —
    # is what bounds the request.
    #
    # Ten minutes, not the minute this started life as: the scripts people
    # actually write query a warehouse, and a warehouse answers when it answers.
    # Two consequences worth knowing before lowering or raising it again. It is a
    # *held HTTP request* — nginx has to be willing to wait at least this long
    # (frontend/nginx.conf.template), or the run is killed by the proxy and the
    # user is told nothing useful. And it multiplies straight into the queue: with
    # `script_max_concurrent_runs` slots, the person who presses Run third waits
    # this long behind the two ahead of them, with no progress to look at.
    script_wall_clock_s: int = 600
    script_max_queries: int = 50
    # stdout+stderr kept per stream; the rest is dropped with a notice, because
    # "read it all" is a memory bomb with a friendly face.
    script_max_output_chars: int = 256 * 1024
    script_memory_mb: int = 1024
    # There is one uvicorn worker. Each run forks a process and holds a worker
    # thread for up to `script_wall_clock_s`, so this is the number of people who
    # can press Run at once before the rest queue — deliberately small.
    script_max_concurrent_runs: int = 2

    @field_validator("judge_score_threshold", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        # docker-compose passes unset optional vars through as "", which would
        # otherwise fail float parsing instead of meaning "no threshold".
        if isinstance(value, str) and not value.strip():
            return None
        return value


settings = Settings()


# Every seam switch, in the order the startup line and the preflight print them.
SEAM_SETTINGS = (
    ("agent", "AGENT_IMPL"),
    ("judge", "JUDGE_IMPL"),
    ("trace", "TRACE_IMPL"),
    ("diagnosis", "DIAGNOSIS_IMPL"),
    ("synthesis", "SYNTHESIS_IMPL"),
    ("workspace", "WORKSPACE_IMPL"),
    ("optimizer", "OPTIMIZER_IMPL"),
)


def seam_impls() -> dict[str, str]:
    """Which implementation each seam is running, by seam name."""
    return {
        name: getattr(settings, f"{name}_impl") for name, _ in SEAM_SETTINGS
    }


def env_file_overrides() -> dict[str, tuple[str, str]]:
    """Settings a `.env` next to the process sets and the environment overrules.

    pydantic-settings ranks the process environment *above* `env_file`, and
    `docker-compose.yml` passes a value for every setting it knows about —
    including its own `${VAR:-fake}` defaults. So editing a `.env` that compose
    does not read (the one under `backend/`, when the value has to be in the
    repo-root one, which is the file compose interpolates from) changes nothing
    at all, and nothing anywhere says why: the file is right there in the
    container, correctly spelled, and simply outranked.

    Returns `{KEY: (what the file says, what is in force)}` for exactly those
    keys, so startup can name them. Blank file values are skipped — the shipped
    example carries plenty, and "unset in a file, set in the environment" is the
    normal case, not a mistake.

    `python-dotenv` is pydantic-settings' own parser, so this reads the file by
    the same rules that decided to ignore it.
    """
    from dotenv import dotenv_values

    path = Path(str(settings.model_config.get("env_file") or ".env"))
    if not path.is_file():
        return {}
    overridden: dict[str, tuple[str, str]] = {}
    for key, file_value in dotenv_values(path).items():
        if file_value is None or not file_value.strip():
            continue
        env_value = os.environ.get(key)
        if env_value is not None and env_value != file_value:
            overridden[key] = (file_value, env_value)
    return overridden
