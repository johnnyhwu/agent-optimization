"""Per-eval-set judge prompt, and a name for the judge's own failures.

Two independent additions that arrive together because the second only becomes
visible once the first exists.

`eval_sets.judge_system_prompt` / `judge_user_prompt` are **nullable with no
default**, and that is the whole design decision: NULL means "use the prompt
that ships with the code". Backfilling the current default text into every row
would have frozen today's wording into every existing set, so a later
improvement to it would reach nobody. The frozen copy belongs on the *run*
(`runs.config`, written at trigger time), where a historical record needs it.

`question_results.failure_kind` names which step failed. Every existing row is
left NULL rather than guessed at from its error message: a run that predates
this column genuinely does not know, and inventing 'agent' for all of them would
make the new "could not be judged" count lie about history.

Revision ID: 0006_judge_prompt
Revises: 0005_list_indexes
Create Date: 2026-08-05
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_judge_prompt"
down_revision = "0005_list_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("eval_sets", sa.Column("judge_system_prompt", sa.Text(), nullable=True))
    op.add_column("eval_sets", sa.Column("judge_user_prompt", sa.Text(), nullable=True))
    op.add_column(
        "eval_sets",
        sa.Column("judge_prompt_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "eval_sets", sa.Column("judge_prompt_verified_model", sa.Text(), nullable=True)
    )
    op.add_column(
        "eval_sets",
        sa.Column("judge_prompt_reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("question_results", sa.Column("failure_kind", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("question_results", "failure_kind")
    op.drop_column("eval_sets", "judge_prompt_reviewed_at")
    op.drop_column("eval_sets", "judge_prompt_verified_model")
    op.drop_column("eval_sets", "judge_prompt_verified_at")
    op.drop_column("eval_sets", "judge_user_prompt")
    op.drop_column("eval_sets", "judge_system_prompt")
