"""Shared query helpers for routers."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import QuestionResult, Run
from app.services.aggregation import RunVerdicts


async def load_run_verdicts(
    session: AsyncSession, runs: list[Run]
) -> list[RunVerdicts]:
    """Build RunVerdicts (question_pk -> verdict) for the given runs, preserving
    the input order (caller passes newest-first when needed)."""
    if not runs:
        return []
    run_ids = [r.id for r in runs]
    rows = (
        await session.execute(
            select(QuestionResult.run_id, QuestionResult.question_pk, QuestionResult.verdict)
            .where(QuestionResult.run_id.in_(run_ids))
        )
    ).all()
    by_run: dict[uuid.UUID, dict[uuid.UUID, str]] = {r.id: {} for r in runs}
    for run_id, qpk, verdict in rows:
        if verdict is not None:
            by_run[run_id][qpk] = verdict
    return [
        RunVerdicts(run_id=r.id, started_at=r.started_at, verdicts=by_run[r.id])
        for r in runs
    ]
