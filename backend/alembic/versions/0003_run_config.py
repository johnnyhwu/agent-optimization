"""Per-run eval configuration (agent / LLM / Langfuse settings chosen in the UI).

Until now every run in a deployment shared one process-wide configuration read
from the environment. A run now records the configuration it was started with,
which is what lets the UI configure a run at trigger time and lets the view path
(trace fetch, re-diagnose) reach the same endpoints months later.

  runs.name     developer-supplied label; falls back to started_at in the UI
  runs.config   non-secret settings — base URLs, models, timeouts, concurrency
  runs.secrets  credentials, kept in their own column

`config` and `secrets` are deliberately two columns rather than one blob: no
response model ever reads `secrets`, so "credentials never leave the server" is
a structural property rather than a field list somebody has to remember to
maintain. Both are objects keyed by setting name; unknown/blank keys fall back
to the environment at run time, so existing rows ('{}') behave exactly as before.

Revision ID: 0003_run_config
Revises: 0002_real_integration
Create Date: 2026-07-29
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003_run_config"
down_revision = "0002_real_integration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("name", sa.Text(), nullable=True))
    op.add_column(
        "runs",
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "runs",
        sa.Column(
            "secrets",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("runs", "secrets")
    op.drop_column("runs", "config")
    op.drop_column("runs", "name")
