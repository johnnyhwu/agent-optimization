"""Record which agent an eval run measured, at its start and at its end.

A run's pass rate is not read on its own. The eval set card draws a sparkline
across runs, the detail view names a regression against the previous one, and
the whole workflow described in the README — optimise, download the skill, put
it on the agent, re-run a normal eval — is a comparison between two runs. Every
one of those readings assumes the thing being measured held still.

Nothing checked that it did. A deploy to the agent server halfway through a
two-hundred-question run makes the questions before and after it measurements of
two different systems, and the only symptom is the number moving — which is
precisely what the comparison exists to show. An optimization run has recorded
its agent's version per step since it was written, for exactly this reason. An
eval run recorded nothing at all.

Two columns rather than one, because one cannot express the thing worth knowing.
A single version pinned at the start says what the run *began* against and stays
silent about a redeploy; a single version taken at the end says what it *ended*
against and silently rewrites history. Only the pair can disagree, and their
disagreement is the whole signal.

Both nullable, and they stay null far more often than not: a fake-mode run has
no agent to ask, an agent that does not answer costs the run a caveat rather
than its results, and every run that already exists predates the question. A
null here means "not known", never "unchanged".

Revision ID: 0015_run_workspace_version
Revises: 0014_user_settings
Create Date: 2026-08-24
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_run_workspace_version"
down_revision = "0014_user_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runs",
        # What the agent server reported when the run was triggered.
        sa.Column("workspace_version", sa.Text(), nullable=True),
    )
    op.add_column(
        "runs",
        # And what it reported once the last question had been answered. Equal
        # to the column above on a run nobody disturbed.
        sa.Column("workspace_version_end", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("runs", "workspace_version_end")
    op.drop_column("runs", "workspace_version")
