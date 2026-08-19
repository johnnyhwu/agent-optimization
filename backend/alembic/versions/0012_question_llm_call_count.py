"""How many model calls one question cost.

The question list showed what a question asked, what it was graded, and how long
it took — but not how much work the agent did to get there. Two questions that
both take nine seconds are not the same question if one made a single model call
and the other made eleven, and that difference is the first thing worth knowing
when a run gets expensive or slow.

The figure is not derivable after the fact from anything this database holds:
spans live in the trace store and are fetched on demand, so answering it for a
list of sixty questions would mean sixty Langfuse round trips every time the page
opened. The orchestrator already fetches each question's trace once while the run
executes, for the diagnosis path, so the count is taken there and written down.

Nullable, and left null for every run that finished before this column existed —
the traces those runs read are long gone from our side, and a zero would read as
"the agent made no model calls" rather than as "we were not counting yet".

Revision ID: 0012_question_llm_call_count
Revises: 0011_optimization_stage_calls
Create Date: 2026-08-19
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_question_llm_call_count"
down_revision = "0011_optimization_stage_calls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "question_results",
        sa.Column("llm_call_count", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("question_results", "llm_call_count")
