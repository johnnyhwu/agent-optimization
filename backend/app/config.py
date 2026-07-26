"""Application settings (non-latency).

All *latency* / fake-timing knobs live in `app/fake_config.py` — the single file
required by TASK.md. This module holds everything else: DB URLs, the fake-login
switch, CORS, and the §6.7 span-body truncation limit.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


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


settings = Settings()
