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
