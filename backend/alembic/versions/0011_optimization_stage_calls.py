"""What the optimizer was asked after the analysts: merge, then rank.

A step's applied edits are not the edits any analyst proposed. The per-minibatch
patches are merged hierarchically (failures together, successes together, then
the two groups combined with failures taking priority) and, if the pool is still
over the learning rate, a ranking call picks which survive. Every one of those is
a model call that can drop an edit or rewrite it, and none of them was stored —
so the rollout page could show what each analyst asked for and what the skill
ended up with, with nothing in between.

One new table, nothing existing touched, in the shape 0009 established: additive
is a property of Optimize's schema, not a property of the first migration that
happened to create it. `tests/test_optimizer_isolation.py` asserts that, and
lists this file.

Older steps have no rows here and the page says so. Backfilling is impossible in
principle — the prompts were never written down.

Revision ID: 0011_optimization_stage_calls
Revises: 0010_rollout_mean_latency
Create Date: 2026-08-16
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0011_optimization_stage_calls"
down_revision = "0010_rollout_mean_latency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "optimization_stage_calls",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "step_id", UUID(as_uuid=True),
            sa.ForeignKey("optimization_steps.id", ondelete="CASCADE"), nullable=False,
        ),
        # Submission order within the step. The calls run on a thread pool, so
        # completion order says nothing about the order the stages happened in.
        sa.Column("seq", sa.Integer(), nullable=False),
        # merge_failure | merge_success | merge_final | ranking
        sa.Column("stage", sa.Text(), nullable=False),
        # Which round of the hierarchical merge. Null where a stage runs once.
        sa.Column("level", sa.Integer(), nullable=True),
        sa.Column("prompt_system", sa.Text(), nullable=True),
        sa.Column("prompt_user", sa.Text(), nullable=True),
        # Parsed, not raw: these stages are JSON contracts, and a reply that
        # could not be parsed is a discarded patch with a reason in `error`.
        sa.Column("output", JSONB, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.UniqueConstraint("step_id", "seq", name="uq_optimization_stage_calls_seq"),
    )
    op.create_index(
        "ix_optimization_stage_calls_step",
        "optimization_stage_calls",
        ["step_id", "seq"],
    )


def downgrade() -> None:
    op.drop_index("ix_optimization_stage_calls_step", "optimization_stage_calls")
    op.drop_table("optimization_stage_calls")
