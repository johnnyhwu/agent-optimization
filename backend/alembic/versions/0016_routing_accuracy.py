"""Measure a routing run against the skills each question was supposed to reach.

`routing` mode optimises the description that decides *when* an agent opens a
skill, and until now it was gated on the target skill's activation rate not
falling. That guard exists because routing has a way to improve accuracy nobody
wants — narrow the description until the agent stops opening the skill at all,
and every question it was answering badly gets answered from the model's own
knowledge instead — but it only ever watched one skill, and it watched it in one
direction. A description widened until it wins every question scores perfectly
by it, while starving every other skill on the agent.

What the mode is actually trying to improve is whether the agent reaches for the
*right* skill, and that is measurable directly: every question already carries
the skill tags it belongs to. Three columns is all it takes.

`ground_truth_skills` on the item, because a run is a comparison and the thing
being compared against has to hold still — the question, its gold answer and now
its tags are pinned when the run starts, so retagging a question tomorrow cannot
change what a run finished today has been plotting since step 0.

`routing_hard` / `routing_soft` beside the judge's `hard` / `soft` on the
rollout rather than replacing them. A routing run still buys a judge verdict for
every question and still draws it: "routing is fixed but the answers did not get
better" is precisely the finding that says to go and run an isolated one next,
and it is invisible if only the gating number is kept.

All three are nullable and null means "this run did not record it" — never zero,
and never the empty list. Every run that already exists predates the question.

Revision ID: 0016_routing_accuracy
Revises: 0015_run_workspace_version
Create Date: 2026-08-30
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0016_routing_accuracy"
down_revision = "0015_run_workspace_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "optimization_items",
        sa.Column("ground_truth_skills", JSONB(), nullable=True),
    )
    op.add_column(
        "optimization_rollouts",
        sa.Column("routing_hard", sa.Numeric(), nullable=True),
    )
    op.add_column(
        "optimization_rollouts",
        sa.Column("routing_soft", sa.Numeric(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("optimization_rollouts", "routing_soft")
    op.drop_column("optimization_rollouts", "routing_hard")
    op.drop_column("optimization_items", "ground_truth_skills")
