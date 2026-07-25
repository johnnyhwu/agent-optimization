"""Question edit endpoint (locked set: edit only — no add/delete).

Editing keeps question_id immutable and bumps version under optimistic lock
(§6.11 / §6.16). Owner only.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_owner
from app.db import get_session
from app.models import Question, QuestionSkill
from app.schemas import QuestionOut, QuestionUpdate

router = APIRouter(prefix="/eval-sets/{eval_set_id}/questions", tags=["questions"])


@router.patch("/{question_pk}", response_model=QuestionOut)
async def update_question(
    eval_set_id: uuid.UUID,
    question_pk: uuid.UUID,
    payload: QuestionUpdate,
    subject: str = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
):
    values: dict = {"version": Question.version + 1}
    if payload.question is not None:
        values["question"] = payload.question
    if payload.ground_truth_response is not None:
        values["ground_truth_response"] = payload.ground_truth_response
    if payload.ground_truth_reasoning is not None:
        values["ground_truth_reasoning"] = payload.ground_truth_reasoning

    res = await session.execute(
        update(Question)
        .where(
            Question.id == question_pk,
            Question.eval_set_id == eval_set_id,
            Question.version == payload.version,
        )
        .values(**values)
        .returning(Question.id)
    )
    if res.first() is None:
        # Either the version is stale (someone else edited) or the question does
        # not belong to this set. Disambiguate with a lookup.
        exists = await session.scalar(
            select(Question.id).where(
                Question.id == question_pk, Question.eval_set_id == eval_set_id
            )
        )
        if exists is None:
            raise HTTPException(status_code=404, detail="question not found in this set")
        raise HTTPException(
            status_code=409,
            detail="This question was modified by someone else. Reload and retry.",
        )
    await session.commit()

    q = await session.get(Question, question_pk)
    skills = (
        await session.scalars(
            select(QuestionSkill.skill_name)
            .where(QuestionSkill.question_pk == q.id)
            .order_by(QuestionSkill.ordinal.asc())
        )
    ).all()
    return QuestionOut(
        id=q.id, question_id=q.question_id, question=q.question,
        ground_truth_response=q.ground_truth_response,
        ground_truth_reasoning=q.ground_truth_reasoning,
        skills=list(skills), version=q.version,
    )
