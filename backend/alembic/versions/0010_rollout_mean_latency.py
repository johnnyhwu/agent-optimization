"""Mean agent latency per rollout.

The step card compares the training and validation splits side by side, and
latency is one of the rows it compares. It had min, median and max; the mean is
what a reader actually asks for first, and it is the one figure that cannot be
recovered from the other three.

That is the whole reason this is a stored column rather than a computed one.
Median and mean answer different questions, and the gap between them is the
answer to "was this rollout slow throughout, or was it one question hanging
until the timeout?" — a distinction min/median/max cannot make on its own.
Deriving it later from `optimization_results` would work only for as long as
every result row is still on disk, which is not a property a summary should
depend on.

Additive and nullable, like 0009 before it: rollouts recorded before this
migration keep their three figures and report the mean as unknown rather than as
a number nobody measured.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_rollout_mean_latency"
down_revision = "0009_optimization"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "optimization_rollouts",
        sa.Column("latency_mean_ms", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("optimization_rollouts", "latency_mean_ms")
