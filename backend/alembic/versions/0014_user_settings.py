"""One developer's own defaults for the forms in this product.

Every form in the product opens on the deployment's values — the agent server in
`AGENT_BASE_URL`, the grading model in `JUDGE_MODEL`, the batch size in
`OPTIMIZER_BATCH_SIZE`. Right for a deployment, wrong for a person: someone who
points every run at their own agent server retypes the same address a dozen
times a day. This table is where they say it once.

One row per subject, and no row for anyone who has not opened the settings page.
That absence is meaningful rather than incidental: the row is created by that
first visit, carrying every setting that exists at the time in `seen_keys`, and
that snapshot is the baseline the "new setting available" hint is measured
against. Creating rows on some earlier read path instead would make a first visit
look like a release full of new settings.

Nothing here changes behaviour on its own. An empty table resolves to exactly
what the environment resolved to before it existed.

Revision ID: 0014_user_settings
Revises: 0013_optimization_stop_reason
Create Date: 2026-08-22
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0014_user_settings"
down_revision = "0013_optimization_stop_reason"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_settings",
        # Normalised (casefolded) subject, the same string `eval_set_roles` is
        # keyed on. A primary key rather than a unique index because two browser
        # tabs opening the settings page at once both try to create this row,
        # and the insert relies on the conflict.
        sa.Column("subject", sa.Text(), primary_key=True),
        sa.Column(
            "values", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "system_at_set", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        # Encrypted at rest (services/user_secrets.py) and, like `runs.secrets`,
        # in a column no response model reads.
        sa.Column(
            "secrets", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "seen_keys", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("user_settings")
