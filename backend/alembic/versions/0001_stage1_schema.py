"""Stage 1 app DB schema (spec §6.14).

Exactly the seven tables: eval_sets, questions, question_skills, runs,
question_results, span_analyses, eval_set_roles. Langfuse remains the source of
truth for trace/span/score; this DB stores only app-owned concepts + the
correlation_id index back into Langfuse.

Revision ID: 0001_stage1_schema
Revises:
Create Date: 2026-07-25
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0001_stage1_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # gen_random_uuid() lives in pgcrypto.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "eval_sets",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source_format", sa.Text(), nullable=False),  # 'csv' | 'jsonl'
        sa.Column("metadata", pg.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )

    op.create_table(
        "questions",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("eval_set_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("eval_sets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question_id", sa.Text(), nullable=False),  # §6.11 immutable, user-visible
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("ground_truth_response", sa.Text(), nullable=False),
        sa.Column("ground_truth_reasoning", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("eval_set_id", "question_id"),
    )

    op.create_table(
        "question_skills",
        sa.Column("question_pk", pg.UUID(as_uuid=True),
                  sa.ForeignKey("questions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("skill_name", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),  # Stage 1 uses ordinal=0
        sa.PrimaryKeyConstraint("question_pk", "ordinal"),
    )

    op.create_table(
        "runs",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("eval_set_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("eval_sets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("triggered_by", sa.Text(), nullable=False),  # token subject
        sa.Column("status", sa.Text(), nullable=False),  # running|completed|failed
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("pass_rate", sa.Numeric(), nullable=True),  # stored on completion
        sa.Column("total_count", sa.Integer(), nullable=True),
        sa.Column("correct_count", sa.Integer(), nullable=True),
    )

    op.create_table(
        "question_results",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("run_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question_pk", pg.UUID(as_uuid=True),
                  sa.ForeignKey("questions.id"), nullable=False),
        sa.Column("correlation_id", sa.Text(), nullable=False),  # -> Langfuse trace
        sa.Column("verdict", sa.Text(), nullable=True),  # correct|incorrect
        sa.Column("judge_score", sa.Numeric(), nullable=True),
        sa.Column("judge_comment", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),  # pending|done|failed
        sa.Column("trace_ready", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("run_id", "question_pk"),
    )

    op.create_table(
        "span_analyses",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("question_result_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("question_results.id", ondelete="CASCADE"), nullable=False),
        sa.Column("overall_diagnosis", sa.Text(), nullable=False),
        sa.Column("caveat", sa.Text(), nullable=True),  # §6.8 cross-stage signal
        sa.Column("raw_llm_output", pg.JSONB(), nullable=False),  # full JSON incl suspects[]
        sa.Column("generated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("model_used", sa.Text(), nullable=False),
        sa.UniqueConstraint("question_result_id"),
    )

    op.create_table(
        "eval_set_roles",
        sa.Column("eval_set_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("eval_sets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_subject", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),  # owner | viewer
        sa.PrimaryKeyConstraint("eval_set_id", "user_subject"),
    )


def downgrade() -> None:
    op.drop_table("eval_set_roles")
    op.drop_table("span_analyses")
    op.drop_table("question_results")
    op.drop_table("runs")
    op.drop_table("question_skills")
    op.drop_table("questions")
    op.drop_table("eval_sets")
