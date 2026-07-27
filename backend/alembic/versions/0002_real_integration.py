"""Columns the real integrations need (§9.2 fake -> real).

With the fake layer nobody missed these: outcomes were synthetic and failures
were simulated. Against a real A2A agent and a real LLM judge they are the
difference between "you can see the eval result" and "you can see a verdict":

  question_results.agent_response    what the agent actually answered
  question_results.error_message     why status='failed' (was: no reason at all)
  question_results.agent_latency_ms  real agent round-trip time
  runs.error_message                 why a whole run ended as failed

Revision ID: 0002_real_integration
Revises: 0001_stage1_schema
Create Date: 2026-07-27
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_real_integration"
down_revision = "0001_stage1_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("question_results", sa.Column("agent_response", sa.Text(), nullable=True))
    op.add_column("question_results", sa.Column("error_message", sa.Text(), nullable=True))
    op.add_column("question_results", sa.Column("agent_latency_ms", sa.Integer(), nullable=True))
    op.add_column("runs", sa.Column("error_message", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "error_message")
    op.drop_column("question_results", "agent_latency_ms")
    op.drop_column("question_results", "error_message")
    op.drop_column("question_results", "agent_response")
