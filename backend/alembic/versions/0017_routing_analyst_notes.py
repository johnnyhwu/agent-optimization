"""Two things a routing step learns that nothing could previously carry upward.

A routing run rewrites descriptions, and there are two ways for it to spend
twenty steps producing nothing while looking entirely healthy on screen.

**The cause is outside the descriptions.** The agent's system prompt tells it to
answer directly, or carries routing rules of its own that override what any
description says. The analyst can see this — its prompt now shows it the agent's
setup and asks it to say so rather than compensate — but until now the
observation went into `patch.reasoning` inside a minibatch's raw JSON, which is
three clicks deep on a page nobody opens when the chart is merely flat. The
symptom a developer actually sees is a column of "0 edits applied", and nothing
anywhere says why. `routing_blocked_by` is that sentence, on the step, where the
overview can read it.

**The questions were not all answered by the same agent.** A step scores one
routing accuracy over its whole batch and hands the analyst one confusion
matrix, both of which assume every question ran under the same setup. Usually
they did, and where they differ it is a timestamp or a workspace id — which is
why this is not simply "the prompts were not identical". `setup_divergence` is
set only when the variants differ too much to show as one, i.e. when the step
really did average two systems. It stores what the warning needs to be specific:
how many prompts, how many variants, and what share the majority held.

Both nullable, and null means "this run did not record it" — not "nothing was
wrong". Every run that already exists predates the question, and both are
routing-only besides.

Revision ID: 0017_routing_analyst_notes
Revises: 0016_routing_accuracy
Create Date: 2026-09-01
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0017_routing_analyst_notes"
down_revision = "0016_routing_accuracy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "optimization_steps",
        sa.Column("routing_blocked_by", sa.Text(), nullable=True),
    )
    op.add_column(
        "optimization_steps",
        sa.Column("setup_divergence", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("optimization_steps", "setup_divergence")
    op.drop_column("optimization_steps", "routing_blocked_by")
