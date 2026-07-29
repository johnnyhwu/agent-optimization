"""Eval-set endpoints: create (JSONL payload), list cards, edit metadata, list
questions, delete."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_subject, require_owner, require_reader
from app.db import get_session
from app.models import (
    EvalSet,
    EvalSetRole,
    Question,
    QuestionSkill,
    Run,
)
from app.routers._helpers import load_run_verdicts
from app.schemas import (
    EvalSetCard,
    EvalSetCreate,
    EvalSetUpdate,
    QuestionOut,
    RolesUpdate,
    ShareEntry,
)
from app.services.aggregation import regression_summary
from app.services.deletion import delete_eval_set as delete_eval_set_rows
from app.services.upload import parse_jsonl

router = APIRouter(prefix="/eval-sets", tags=["eval-sets"])


async def _build_card(session: AsyncSession, es: EvalSet, subject: str) -> EvalSetCard:
    runs = (
        await session.scalars(
            select(Run).where(Run.eval_set_id == es.id).order_by(Run.started_at.asc())
        )
    ).all()
    trend = [float(r.pass_rate) if r.pass_rate is not None else None for r in runs]
    latest = next(
        (float(r.pass_rate) for r in reversed(runs) if r.pass_rate is not None), None
    )
    newest_first = list(reversed(runs))
    verdicts = await load_run_verdicts(session, newest_first)
    reg = regression_summary(verdicts)
    role_rows = (
        await session.execute(
            select(EvalSetRole.user_subject, EvalSetRole.role)
            .where(EvalSetRole.eval_set_id == es.id)
            .order_by(EvalSetRole.role.asc(), EvalSetRole.user_subject.asc())
        )
    ).all()
    roles = [ShareEntry(subject=s, role=r) for s, r in role_rows]
    my_role = next((r.role for r in roles if r.subject == subject), None)
    return EvalSetCard(
        id=es.id, name=es.name, description=es.description, metadata=es.meta,
        version=es.version, created_at=es.created_at, updated_at=es.updated_at,
        run_count=len(runs), latest_pass_rate=latest, trend=trend,
        regressed=reg["regressed"], improved=reg["improved"], my_role=my_role,
        roles=roles,
    )


def _clean_shares(shares: list[ShareEntry], creator: str) -> dict[str, str]:
    """Normalize a share list into {subject: role}, always keeping the creator as
    owner and dropping empty/invalid rows."""
    desired: dict[str, str] = {}
    for s in shares:
        subj = (s.subject or "").strip()
        if not subj or s.role not in ("owner", "viewer"):
            continue
        desired[subj] = s.role
    desired[creator] = "owner"  # creator/actor can never lock themselves out
    return desired


@router.post("", status_code=201)
async def create_eval_set(
    payload: EvalSetCreate,
    subject: str = Depends(current_subject),
    session: AsyncSession = Depends(get_session),
):
    """Create an eval set from JSONL question lines. A CSV upload is parsed and
    converted to JSONL client-side (§9.1); `source_format` records which format the
    developer actually uploaded. Creator becomes owner. Set is LOCKED afterward
    (no add/delete question endpoints exist)."""
    parsed = parse_jsonl(payload.jsonl)
    if parsed.errors:
        raise HTTPException(status_code=422, detail={"upload_errors": parsed.errors})

    es = EvalSet(
        name=payload.name, description=payload.description,
        source_format=payload.source_format, meta=payload.metadata,
    )
    session.add(es)
    await session.flush()  # get es.id

    # Creator is owner; apply any additional share grants.
    for subj, role in _clean_shares(payload.shares, subject).items():
        session.add(EvalSetRole(eval_set_id=es.id, user_subject=subj, role=role))
    for pq in parsed.questions:
        q = Question(
            eval_set_id=es.id, question_id=pq.question_id, question=pq.question,
            ground_truth_response=pq.ground_truth_response,
            ground_truth_reasoning=pq.ground_truth_reasoning,
        )
        session.add(q)
        await session.flush()
        for ordinal, skill in enumerate(pq.skills):
            session.add(QuestionSkill(question_pk=q.id, skill_name=skill, ordinal=ordinal))
    await session.commit()
    return {"id": str(es.id), "question_count": len(parsed.questions)}


@router.get("", response_model=list[EvalSetCard])
async def list_eval_sets(
    subject: str = Depends(current_subject),
    session: AsyncSession = Depends(get_session),
):
    """Cards for every eval set the caller has a role on (§6.13 top tier)."""
    es_ids = (
        await session.scalars(
            select(EvalSetRole.eval_set_id).where(EvalSetRole.user_subject == subject)
        )
    ).all()
    if not es_ids:
        return []
    sets = (
        await session.scalars(
            select(EvalSet).where(EvalSet.id.in_(es_ids)).order_by(EvalSet.created_at.desc())
        )
    ).all()
    return [await _build_card(session, es, subject) for es in sets]


@router.get("/metadata/keys", response_model=list[str])
async def known_metadata_keys(
    subject: str = Depends(current_subject),
    session: AsyncSession = Depends(get_session),
):
    """Distinct custom metadata keys across the caller's sets (§6.10: auto-suggest
    existing keys). Served by scanning the JSONB column."""
    es_ids = (
        await session.scalars(
            select(EvalSetRole.eval_set_id).where(EvalSetRole.user_subject == subject)
        )
    ).all()
    if not es_ids:
        return []
    metas = (
        await session.scalars(select(EvalSet.meta).where(EvalSet.id.in_(es_ids)))
    ).all()
    keys: set[str] = set()
    for m in metas:
        if isinstance(m, dict):
            keys.update(m.keys())
    return sorted(keys)


@router.get("/{eval_set_id}", response_model=EvalSetCard)
async def get_eval_set(
    eval_set_id: uuid.UUID,
    subject: str = Depends(require_reader),
    session: AsyncSession = Depends(get_session),
):
    es = await session.get(EvalSet, eval_set_id)
    if es is None:
        raise HTTPException(status_code=404, detail="eval set not found")
    return await _build_card(session, es, subject)


@router.patch("/{eval_set_id}", response_model=EvalSetCard)
async def update_eval_set(
    eval_set_id: uuid.UUID,
    payload: EvalSetUpdate,
    subject: str = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
):
    """Optimistic-locked edit of name/description/metadata (§6.16)."""
    # Use mapped-attribute keys (not string column names): the DB column is
    # "metadata", which collides with SQLAlchemy's reserved MetaData attr — the
    # ORM attribute for it is `EvalSet.meta`.
    values: dict = {EvalSet.version: EvalSet.version + 1, EvalSet.updated_at: func.now()}
    if payload.name is not None:
        values[EvalSet.name] = payload.name
    if payload.description is not None:
        values[EvalSet.description] = payload.description
    if payload.metadata is not None:
        values[EvalSet.meta] = payload.metadata

    res = await session.execute(
        update(EvalSet)
        .where(EvalSet.id == eval_set_id, EvalSet.version == payload.version)
        .values(values)
        .returning(EvalSet.id)
    )
    if res.first() is None:
        raise HTTPException(
            status_code=409,
            detail="This eval set was modified by someone else. Reload and retry.",
        )
    await session.commit()
    es = await session.get(EvalSet, eval_set_id)
    return await _build_card(session, es, subject)


@router.delete("/{eval_set_id}", status_code=204)
async def delete_eval_set(
    eval_set_id: uuid.UUID,
    subject: str = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
):
    """Delete an eval set with all of its runs, results and diagnoses (owner-only).

    Refused while a run is in flight: the orchestrator is a background task still
    writing to those rows, and deleting underneath it would leave the run writing
    to nothing. The stop button is the way out.
    """
    es = await session.get(EvalSet, eval_set_id)
    if es is None:
        raise HTTPException(status_code=404, detail="eval set not found")

    running = await session.scalar(
        select(func.count())
        .select_from(Run)
        .where(Run.eval_set_id == eval_set_id, Run.status == "running")
    )
    if running:
        raise HTTPException(
            status_code=409,
            detail="cancel the run(s) still executing before deleting this eval set",
        )

    await delete_eval_set_rows(session, eval_set_id)
    await session.commit()
    return Response(status_code=204)


@router.put("/{eval_set_id}/roles", response_model=EvalSetCard)
async def update_roles(
    eval_set_id: uuid.UUID,
    payload: RolesUpdate,
    subject: str = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
):
    """Replace the share list (owner-only). The acting owner always stays an owner,
    so a set can never end up with zero owners / lock its editor out."""
    es = await session.get(EvalSet, eval_set_id)
    if es is None:
        raise HTTPException(status_code=404, detail="eval set not found")

    desired = _clean_shares(payload.shares, subject)
    await session.execute(
        delete(EvalSetRole).where(EvalSetRole.eval_set_id == eval_set_id)
    )
    for subj, role in desired.items():
        session.add(EvalSetRole(eval_set_id=eval_set_id, user_subject=subj, role=role))
    await session.commit()
    return await _build_card(session, es, subject)


@router.get("/{eval_set_id}/questions", response_model=list[QuestionOut])
async def list_questions(
    eval_set_id: uuid.UUID,
    subject: str = Depends(require_reader),
    session: AsyncSession = Depends(get_session),
):
    questions = (
        await session.scalars(
            select(Question)
            .where(Question.eval_set_id == eval_set_id)
            .order_by(Question.created_at.asc())
        )
    ).all()
    out = []
    for q in questions:
        skills = (
            await session.scalars(
                select(QuestionSkill.skill_name)
                .where(QuestionSkill.question_pk == q.id)
                .order_by(QuestionSkill.ordinal.asc())
            )
        ).all()
        out.append(
            QuestionOut(
                id=q.id, question_id=q.question_id, question=q.question,
                ground_truth_response=q.ground_truth_response,
                ground_truth_reasoning=q.ground_truth_reasoning,
                skills=list(skills), version=q.version,
            )
        )
    return out
