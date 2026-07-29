"""Run cancellation + the two error fields that were previously only logged.

Three columns, each closing a gap that only shows up once the seams are real
services rather than fakes that never fail or take long:

  runs.cancel_requested          A run against a real agent can take minutes; a
                                 mis-clicked one had no way out but restarting
                                 the backend. Persisted (not just in-memory) so
                                 the reason a run stopped survives a restart.
  question_results.trace_error   Why fetching the trace failed (Langfuse
                                 unreachable / 401 / timeout). Without it the UI
                                 shows the same "still ingesting" state for a
                                 misconfigured host as for a trace that really is
                                 seconds away, which is indistinguishable from
                                 the platform being broken.
  question_results.diagnosis_error  Why the diagnosis LLM call failed. It was
                                 log.warning-only, so from the UI an undiagnosed
                                 question looked identical whether the model
                                 errored or was never asked.

No enum changes: `runs.status` gains 'cancelled' and `question_results.status`
gains 'cancelled', and both are plain Text columns.

Revision ID: 0004_run_lifecycle
Revises: 0003_run_config
Create Date: 2026-07-29
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_run_lifecycle"
down_revision = "0003_run_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column(
            "cancel_requested",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column("question_results", sa.Column("trace_error", sa.Text(), nullable=True))
    op.add_column(
        "question_results", sa.Column("diagnosis_error", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("question_results", "diagnosis_error")
    op.drop_column("question_results", "trace_error")
    op.drop_column("runs", "cancel_requested")
