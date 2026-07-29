"""Indexes for the two list surfaces.

Until now the schema carried no indexes beyond primary keys and unique
constraints, which was fine while the only data came from `seed.py`. The three
below are the ones the home page and the run history actually hit; without them
every card render and every run listing is a sequential scan that grows with
total history rather than with what is being shown.

  eval_set_roles(user_subject)         GET /eval-sets starts by asking "which
                                       sets can this subject see". The table's
                                       primary key is (eval_set_id, user_subject),
                                       so a lookup by subject alone can't use it.
  runs(eval_set_id, started_at DESC)   Serves both the run list and the per-set
                                       aggregates (run count, trend, latest two
                                       runs) in one ordered index scan.
  question_results(run_id, verdict)    The incorrect-count aggregate. The unique
                                       constraint on (run_id, question_pk)
                                       already covers lookups by run, but not
                                       counting by verdict without a heap fetch.

Index creation is not concurrent: these tables are small at Stage 1 scale and
CONCURRENTLY cannot run inside Alembic's transaction. Revisit if this ever meets
a large live database.

Revision ID: 0005_list_indexes
Revises: 0004_run_lifecycle
Create Date: 2026-07-29
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_list_indexes"
down_revision = "0004_run_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_eval_set_roles_user_subject", "eval_set_roles", ["user_subject"]
    )
    op.create_index(
        "ix_runs_eval_set_started",
        "runs",
        ["eval_set_id", sa.text("started_at DESC")],
    )
    op.create_index(
        "ix_question_results_run_verdict", "question_results", ["run_id", "verdict"]
    )


def downgrade() -> None:
    op.drop_index("ix_question_results_run_verdict", table_name="question_results")
    op.drop_index("ix_runs_eval_set_started", table_name="runs")
    op.drop_index("ix_eval_set_roles_user_subject", table_name="eval_set_roles")
