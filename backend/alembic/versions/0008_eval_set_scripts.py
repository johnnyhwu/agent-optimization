"""The Python script an eval set was generated from.

An eval set can now be produced by uploading a script that queries a business
database, instead of a CSV or JSONL file. "Where did these questions come from?"
then has an answer only the system can keep, so it keeps it: the script body, a
fingerprint of it, and which database it read — as whom, and when.

A table rather than columns on `eval_sets`, for a specific reason. `_build_cards`
reads a page of eval sets to render the home page and was written to touch a
bounded number of rows; adding a full script body to that row would pull a
kilobyte of Python per card to display something the card does not show. Here it
is read only when someone opens the set and asks.

**No password column, by design.** The credentials a script runs with arrive on
one request, are used to open one connection, and are never written down. What is
recorded is the target and the user — enough to audit or reproduce, and nothing
that would turn a database dump into a credential leak.

`source` is unbounded text on purpose: a length limit here would reject a
legitimate script at commit time, after it has already run successfully, which is
the worst possible moment to find out.

Revision ID: 0008_eval_set_scripts
Revises: 0007_question_started_at
Create Date: 2026-08-10
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_eval_set_scripts"
down_revision = "0007_question_started_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "eval_set_scripts",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "eval_set_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("eval_sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_sha256", sa.Text(), nullable=False),
        sa.Column("db_host", sa.Text(), nullable=False),
        sa.Column("db_port", sa.Integer(), nullable=False),
        sa.Column("db_name", sa.Text(), nullable=False),
        sa.Column("db_user", sa.Text(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("executed_by", sa.Text(), nullable=False),
        sa.Column(
            "executed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # One script per set. A set is locked once created (§6.11), so there is
        # exactly one run behind it and no route by which a second could appear —
        # the constraint states that rather than leaving it to convention.
        sa.UniqueConstraint("eval_set_id", name="uq_eval_set_scripts_eval_set_id"),
    )


def downgrade() -> None:
    op.drop_table("eval_set_scripts")
