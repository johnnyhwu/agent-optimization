"""Optimize (Stage 3): training a skill against eval questions.

Seven new tables and **not one change to an existing one**. That is the whole
shape of this migration and it is deliberate.

An optimization run performs epochs × steps × (train + val) agent calls and
records a verdict for each. That is the same shape as eval data, and it lands in
the same database — so the tempting design is a `kind` column on `runs` and a
nullable `optimization_step_id`, reusing the orchestrator, the results router and
the SSE plumbing for free. It was rejected for two reasons:

  * `runs.eval_set_id` is a single required foreign key, and an optimization run
    can source questions from several eval sets at once. Whichever set were
    named would be a lie in the one column the eval side joins on.
  * `GET /eval-sets` was rewritten to touch a bounded number of rows (spec
    §10.2③: 180 queries → 6). Every eval-set card, sparkline and regression
    summary reads `runs` and `question_results`. Adding rows there that must
    then be filtered out again puts the burden on every future query, and the
    failure mode is silent: an eval set's pass rate drifts for reasons that have
    nothing to do with the agent.

So Optimize points *at* eval data by id and eval data never points back. The
links are `ON DELETE SET NULL`, not CASCADE, and every row carrying a question
also carries its own copy of the text: an optimization run is a historical
record, and deleting a source eval set next month must leave last month's run
readable rather than deleting it.

`tests/test_optimizer_isolation.py` asserts all of this, including — by parsing
this file — that `upgrade()` only ever creates.

Revision ID: 0009_optimization
Revises: 0008_eval_set_scripts
Create Date: 2026-08-13
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0009_optimization"
down_revision = "0008_eval_set_scripts"
branch_labels = None
depends_on = None


def _pk():
    return sa.Column(
        "id", UUID(as_uuid=True), primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def _json(name, nullable=False, default="'{}'::jsonb"):
    return sa.Column(
        name, JSONB, nullable=nullable,
        server_default=sa.text(default) if not nullable else None,
    )


def upgrade() -> None:
    op.create_table(
        "optimization_runs",
        _pk(),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=False),
        # pending | running | completed | failed | cancelled | interrupted
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),  # isolated | routing
        sa.Column("skill_name", sa.Text(), nullable=False),
        # Split exactly as `runs` splits them, so that "no response model can
        # carry a credential outward" stays structural rather than a convention.
        _json("config"),
        _json("secrets"),
        sa.Column("workspace_version", sa.Text(), nullable=True),
        _json("initial_skill"),
        _json("workspace_baseline", nullable=True),
        _json("detector"),
        sa.Column("num_epochs", sa.Integer(), nullable=False),
        sa.Column("batch_size", sa.Integer(), nullable=False),
        sa.Column("steps_per_epoch", sa.Integer(), nullable=False),
        sa.Column("total_steps", sa.Integer(), nullable=False),
        sa.Column("best_step", sa.Integer(), nullable=True),
        sa.Column("best_score", sa.Numeric(), nullable=True),
        sa.Column(
            "cancel_requested", sa.Boolean(), nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # The run list is "mine, newest first" and nothing else.
    op.create_index(
        "ix_optimization_runs_owner_started",
        "optimization_runs", ["created_by", sa.text("started_at DESC")],
    )

    op.create_table(
        "optimization_items",
        _pk(),
        sa.Column(
            "run_id", UUID(as_uuid=True),
            sa.ForeignKey("optimization_runs.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("split", sa.Text(), nullable=False),  # train | val
        # `question_id` is unique per eval set, not globally, and a run may import
        # from several — so the id the algorithm sees is composite.
        sa.Column("item_key", sa.Text(), nullable=False),
        sa.Column(
            "question_pk", UUID(as_uuid=True),
            sa.ForeignKey("questions.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "source_eval_set_id", UUID(as_uuid=True),
            sa.ForeignKey("eval_sets.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("ground_truth_response", sa.Text(), nullable=False),
        sa.Column("ground_truth_reasoning", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prior_accuracy", sa.Numeric(), nullable=True),
        sa.Column("prior_runs", sa.Integer(), nullable=True),
        # Not (run_id, question_pk): the wizard's "duplicate to validation" puts
        # the same question in both splits on purpose.
        sa.UniqueConstraint("run_id", "split", "item_key", name="uq_optimization_items_key"),
    )
    op.create_index("ix_optimization_items_run_split", "optimization_items", ["run_id", "split"])

    op.create_table(
        "optimization_steps",
        _pk(),
        sa.Column(
            "run_id", UUID(as_uuid=True),
            sa.ForeignKey("optimization_runs.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("step_no", sa.Integer(), nullable=False),  # 0 = baseline
        sa.Column("epoch_no", sa.Integer(), nullable=False),
        sa.Column("step_in_epoch", sa.Integer(), nullable=False, server_default="0"),
        # The last *accepted* step, which is the diff's base. A rejected step
        # rolls the skill back, so this is not step_no - 1.
        sa.Column("parent_step_no", sa.Integer(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),  # running | done | aborted
        sa.Column("retried", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("abort_reason", sa.Text(), nullable=True),
        sa.Column("edit_budget", sa.Integer(), nullable=True),
        sa.Column("gate_action", sa.Text(), nullable=True),
        sa.Column("gate_reject_reason", sa.Text(), nullable=True),  # accuracy | activation
        sa.Column("candidate_hash", sa.Text(), nullable=True),
        sa.Column(
            "candidate_from_cache", sa.Boolean(), nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("n_edits_merged", sa.Integer(), nullable=True),
        sa.Column("n_edits_ranked", sa.Integer(), nullable=True),
        sa.Column("n_edits_applied", sa.Integer(), nullable=True),
        sa.Column("n_edits_skipped", sa.Integer(), nullable=True),
        # What became of each proposed edit, as the apply stage reported it.
        # The counts above cannot tell a bad idea from a mistyped target string,
        # and the status is decided during apply — there is no way to work it
        # out afterwards from the before and after snapshots.
        _json("edit_reports", default="'[]'::jsonb"),
        sa.Column("lines_added", sa.Integer(), nullable=True),
        sa.Column("lines_removed", sa.Integer(), nullable=True),
        sa.Column("files_touched", sa.Integer(), nullable=True),
        sa.Column("skill_len", sa.Integer(), nullable=True),
        sa.Column("edit_summary", sa.Text(), nullable=True),
        sa.Column("current_score", sa.Numeric(), nullable=True),
        sa.Column("best_score", sa.Numeric(), nullable=True),
        _json("timings"),
        _json("tokens"),
        sa.Column(
            "started_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("run_id", "step_no", name="uq_optimization_steps_step_no"),
    )

    op.create_table(
        "optimization_rollouts",
        _pk(),
        sa.Column(
            "step_id", UUID(as_uuid=True),
            sa.ForeignKey("optimization_steps.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("split", sa.Text(), nullable=False),  # train | val
        sa.Column("skill_step_no", sa.Integer(), nullable=False),
        sa.Column("n_items", sa.Integer(), nullable=False, server_default="0"),
        # Scored excludes infrastructure failures. An agent timeout is not the
        # skill being wrong, and scoring it as a wrong answer would hand the
        # optimizer a gradient pointing at the network.
        sa.Column("n_scored", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_agent_error", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_judge_error", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("hard", sa.Numeric(), nullable=True),
        sa.Column("soft", sa.Numeric(), nullable=True),
        sa.Column("activation_rate", sa.Numeric(), nullable=True),
        sa.Column("n_activated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_min_ms", sa.Integer(), nullable=True),
        sa.Column("latency_p50_ms", sa.Integer(), nullable=True),
        sa.Column("latency_max_ms", sa.Integer(), nullable=True),
        sa.Column("aborted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("abort_reason", sa.Text(), nullable=True),
        sa.UniqueConstraint("step_id", "split", name="uq_optimization_rollouts_split"),
    )

    op.create_table(
        "optimization_results",
        _pk(),
        sa.Column(
            "rollout_id", UUID(as_uuid=True),
            sa.ForeignKey("optimization_rollouts.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("item_key", sa.Text(), nullable=False),
        sa.Column(
            "question_pk", UUID(as_uuid=True),
            sa.ForeignKey("questions.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("correlation_id", sa.Text(), nullable=False),
        sa.Column("agent_response", sa.Text(), nullable=True),
        sa.Column("agent_latency_ms", sa.Integer(), nullable=True),
        sa.Column("verdict", sa.Text(), nullable=True),
        sa.Column("judge_score", sa.Numeric(), nullable=True),
        sa.Column("judge_comment", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),  # pending | done | failed
        # Same vocabulary as question_results.failure_kind, so the two halves of
        # the product describe a timeout identically.
        sa.Column("failure_kind", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        # Nullable on purpose: "the detectors could not tell" is a third answer,
        # and reporting it as false would read as the agent ignoring the skill.
        sa.Column("activated", sa.Boolean(), nullable=True),
        _json("skills_read", nullable=True),
        sa.Column("detector_hit", sa.Text(), nullable=True),
        sa.Column("trace_ready", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("trace_error", sa.Text(), nullable=True),
        sa.Column("minibatch_no", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.UniqueConstraint("rollout_id", "item_key", name="uq_optimization_results_item"),
    )
    op.create_index("ix_optimization_results_rollout", "optimization_results", ["rollout_id"])

    op.create_table(
        "optimization_minibatches",
        _pk(),
        sa.Column(
            "step_id", UUID(as_uuid=True),
            sa.ForeignKey("optimization_steps.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("minibatch_no", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),  # failure | success
        sa.Column("n_items", sa.Integer(), nullable=False, server_default="0"),
        # The prompt actually sent, i.e. after truncation — which is what makes
        # storing it verbatim safe: its size is bounded by the budget by
        # construction. The untruncated original is not kept; the ledger is.
        sa.Column("prompt_system", sa.Text(), nullable=True),
        sa.Column("prompt_user", sa.Text(), nullable=True),
        _json("raw_output", nullable=True),
        _json("truncation", nullable=True),
        sa.Column("chars_before", sa.Integer(), nullable=True),
        sa.Column("chars_after", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.UniqueConstraint("step_id", "minibatch_no", name="uq_optimization_minibatches_no"),
    )

    op.create_table(
        "optimization_skills",
        _pk(),
        sa.Column(
            "run_id", UUID(as_uuid=True),
            sa.ForeignKey("optimization_runs.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("step_no", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),  # initial | candidate
        # Whole snapshots, not stored diffs: a skill is a few kilobytes, and the
        # diff on screen is computed against an arbitrary base anyway ("vs the
        # previous accepted step" or "vs the initial skill"). Replaying patches
        # would make the displayed diff depend on every earlier step being right.
        _json("files"),
        sa.Column("content_hash", sa.Text(), nullable=False),
        _json("per_file_stats"),
        sa.UniqueConstraint("run_id", "step_no", "kind", name="uq_optimization_skills_step"),
    )


def downgrade() -> None:
    op.drop_table("optimization_skills")
    op.drop_table("optimization_minibatches")
    op.drop_table("optimization_results")
    op.drop_table("optimization_rollouts")
    op.drop_table("optimization_steps")
    op.drop_table("optimization_items")
    op.drop_table("optimization_runs")
