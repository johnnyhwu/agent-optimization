"""Ordered deletion of runs and eval sets.

The order matters and cannot be delegated to Postgres. `question_results
.question_pk -> questions.id` deliberately has no ON DELETE CASCADE (§9.4: a
locked set never deletes questions), so deleting an eval set by cascade alone
would depend on Postgres happening to remove `question_results` (via `runs`)
before it removes `questions` — and that ordering is not guaranteed. Deleting
child rows explicitly, deepest first, makes it deterministic.

`seed.py` already had this sequence inline; it now calls in here so the ordering
knowledge lives in exactly one place.
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    EvalSet,
    EvalSetRole,
    EvalSetScript,
    OptimizationItem,
    OptimizationMinibatch,
    OptimizationResult,
    OptimizationRollout,
    OptimizationRun,
    OptimizationSkill,
    OptimizationStageCall,
    OptimizationStep,
    Question,
    QuestionResult,
    QuestionSkill,
    Run,
    SpanAnalysis,
)


async def _delete_run_rows(session: AsyncSession, run_ids: Sequence[uuid.UUID]) -> None:
    """span_analyses -> question_results -> runs, for the given runs."""
    if not run_ids:
        return
    result_ids = (
        await session.scalars(
            select(QuestionResult.id).where(QuestionResult.run_id.in_(run_ids))
        )
    ).all()
    if result_ids:
        await session.execute(
            delete(SpanAnalysis).where(SpanAnalysis.question_result_id.in_(result_ids))
        )
    await session.execute(delete(QuestionResult).where(QuestionResult.run_id.in_(run_ids)))
    await session.execute(delete(Run).where(Run.id.in_(run_ids)))


async def delete_run(session: AsyncSession, run_id: uuid.UUID) -> None:
    """Delete one run and everything hanging off it. Caller commits."""
    await _delete_run_rows(session, [run_id])


async def delete_optimization_run(session: AsyncSession, run_id: uuid.UUID) -> None:
    """Delete one optimization run and everything under it. Caller commits.

    Here rather than in the router, and as bulk statements rather than as
    `session.delete(run)`, for a reason the eval side never had to face: an
    optimization run is *large*. Every relationship from the run down is
    `cascade="all, delete-orphan"` with no `passive_deletes`, so the ORM path
    loads the whole tree into memory — items, steps, every rollout, and one row
    per question per rollout, which for a sixty-step run over a twenty-question
    batch is tens of thousands of objects — and then issues a DELETE per row.
    The rows are all reachable from three ids, so this is nine statements.

    Deepest first, like `_delete_run_rows` above, and for the same reason: the
    database's own ON DELETE CASCADE would do it, but the order in which
    Postgres walks a cascade is not something to depend on, and an explicit
    order is the one thing here that can be read and tested.

    The run's links *out* — `optimization_items.question_pk`,
    `.source_eval_set_id`, `optimization_results.question_pk` — are all ON DELETE
    SET NULL and point at eval tables. Deleting a run therefore touches nothing
    on the evaluation side, which is the separation `test_optimizer_isolation.py`
    exists to guard.
    """
    step_ids = (
        await session.scalars(
            select(OptimizationStep.id).where(OptimizationStep.run_id == run_id)
        )
    ).all()
    if step_ids:
        rollout_ids = (
            await session.scalars(
                select(OptimizationRollout.id).where(
                    OptimizationRollout.step_id.in_(step_ids)
                )
            )
        ).all()
        if rollout_ids:
            await session.execute(
                delete(OptimizationResult).where(
                    OptimizationResult.rollout_id.in_(rollout_ids)
                )
            )
            await session.execute(
                delete(OptimizationRollout).where(OptimizationRollout.id.in_(rollout_ids))
            )
        await session.execute(
            delete(OptimizationStageCall).where(
                OptimizationStageCall.step_id.in_(step_ids)
            )
        )
        await session.execute(
            delete(OptimizationMinibatch).where(
                OptimizationMinibatch.step_id.in_(step_ids)
            )
        )
        await session.execute(
            delete(OptimizationStep).where(OptimizationStep.id.in_(step_ids))
        )
    await session.execute(delete(OptimizationItem).where(OptimizationItem.run_id == run_id))
    await session.execute(delete(OptimizationSkill).where(OptimizationSkill.run_id == run_id))
    await session.execute(delete(OptimizationRun).where(OptimizationRun.id == run_id))


async def delete_eval_set(session: AsyncSession, eval_set_id: uuid.UUID) -> None:
    """Delete an eval set with all its runs, results, diagnoses and questions.

    Caller commits.
    """
    run_ids = (
        await session.scalars(select(Run.id).where(Run.eval_set_id == eval_set_id))
    ).all()
    await _delete_run_rows(session, run_ids)

    question_ids = (
        await session.scalars(
            select(Question.id).where(Question.eval_set_id == eval_set_id)
        )
    ).all()
    if question_ids:
        await session.execute(
            delete(QuestionSkill).where(QuestionSkill.question_pk.in_(question_ids))
        )
    await session.execute(delete(Question).where(Question.eval_set_id == eval_set_id))
    await session.execute(
        delete(EvalSetRole).where(EvalSetRole.eval_set_id == eval_set_id)
    )
    await session.execute(
        delete(EvalSetScript).where(EvalSetScript.eval_set_id == eval_set_id)
    )
    await session.execute(delete(EvalSet).where(EvalSet.id == eval_set_id))
