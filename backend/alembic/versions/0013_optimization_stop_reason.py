"""Why an optimization run ended.

`status` says what happened to the run — completed, failed, cancelled — and
until now that was the only account of an ending. It was enough while a run had
exactly one way to finish: execute `num_epochs × steps_per_epoch` steps and
stop. It is not enough now that a run can stop early, because every early stop
is also 'completed', and the reason is the result: a run that stopped because
validation reached its target and one that stopped because the agent server
stopped answering are the same row otherwise, and the page has nothing to tell
the reader apart from a step count that is lower than the one they asked for.

Nullable, and left null for every run that finished before early stopping
existed. Backfilling 'finished' onto those would be a claim about runs nobody
measured — an interrupted or failed run of that vintage did not "finish" — and
the UI reads a null as "this run predates the question".

Revision ID: 0013_optimization_stop_reason
Revises: 0012_question_llm_call_count
Create Date: 2026-08-20
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_optimization_stop_reason"
down_revision = "0012_question_llm_call_count"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "optimization_runs",
        sa.Column("stop_reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("optimization_runs", "stop_reason")
