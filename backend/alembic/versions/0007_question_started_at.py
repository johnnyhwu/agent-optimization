"""When a question's agent call actually went out.

The left column counts up while a question is running and shows the settled
duration once it lands, so it needs an instant to count *from*. None of the
existing columns is that instant:

  * `question_results.created_at` is when the row was written, and the
    orchestrator creates every row for a run up front (§6.15) so the question
    list can show the whole set greyed out from the first second. With
    RUN_CONCURRENCY=1 the fiftieth question's row is minutes old before its
    agent call begins, so counting from `created_at` would open that row at
    "running for 40 minutes".
  * `runs.started_at` is the run's, not the question's.
  * `agent_latency_ms` is only written once the agent has answered, which is
    precisely the window the timer is for.

Nullable with no backfill, for the same reason `failure_kind` was in 0006: a
row written before this column existed genuinely does not know when it started,
and inventing a value would put a fabricated duration on historical runs. The
UI shows no timer for those rows, which is the honest answer.

Deliberately not indexed. Nothing filters or orders by it — it is read only as
part of a row already being fetched by run.

Revision ID: 0007_question_started_at
Revises: 0006_judge_prompt
Create Date: 2026-08-08
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_question_started_at"
down_revision = "0006_judge_prompt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "question_results",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("question_results", "started_at")
