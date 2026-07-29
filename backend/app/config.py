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

from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Impl = Literal["fake", "real"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Async URL for the app; sync URL for Alembic migrations.
    database_url: str = "postgresql+asyncpg://agentopt:agentopt@localhost:5432/agentopt"
    sync_database_url: str = "postgresql+psycopg://agentopt:agentopt@localhost:5432/agentopt"

    # Fake logged-in user (§6.16). Role is NOT stored here — it is resolved per
    # eval_set from the `eval_set_roles` table using this subject.
    fake_user_subject: str = "alice"

    # Fake user directory: the selectable identities for the login switch and the
    # share pickers. A real deployment would replace this with the org's directory.
    known_users: list[str] = ["alice", "bob", "carol", "dave"]

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
    # How much of an exception message is kept in question_results.error_message.
    error_message_max_chars: int = 2000

    @field_validator("judge_score_threshold", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        # docker-compose passes unset optional vars through as "", which would
        # otherwise fail float parsing instead of meaning "no threshold".
        if isinstance(value, str) and not value.strip():
            return None
        return value


settings = Settings()
