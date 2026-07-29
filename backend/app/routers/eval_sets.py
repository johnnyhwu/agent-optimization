"""Eval-set endpoints: create (JSONL payload), list cards, edit metadata, list
questions, delete."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_subject, require_owner, require_reader
from app.db import get_session
from app.models import (
    EvalSet,
    EvalSetRole,
    Question,
    QuestionResult,
    QuestionSkill,
    Run,
)
from app.schemas import (
    EvalSetCard,
    EvalSetCreate,
    EvalSetPage,
    EvalSetUpdate,
    QuestionOut,
    RolesUpdate,
    ShareEntry,
)
from app.services.aggregation import RunVerdicts, regression_summary
from app.services.deletion import delete_eval_set as delete_eval_set_rows
from app.services.upload import parse_jsonl

router = APIRouter(prefix="/eval-sets", tags=["eval-sets"])


# How many of a set's most recent runs the sparkline draws. The trend line is a
# glance at recent direction, not an archive: without a cap, one long-lived eval
# set makes the home page load its entire run history to render ~120px of SVG.
TREND_RUNS = 20


async def _build_cards(
    session: AsyncSession, sets: list[EvalSet], subject: str
) -> list[EvalSetCard]:
    """Cards for a page of eval sets in a fixed number of queries.

    Built for the whole page at once, deliberately. The per-set version issued
    three queries each, one of which pulled *every question_result of every run*
    of that set purely to compute a two-run regression summary — so a home page
    with fifty sets of real history read hundreds of thousands of rows to render
    a handful of numbers. Everything below is bounded by the page, not by history.
    """
    if not sets:
        return []
    set_ids = [es.id for es in sets]

    # 1. Run count per set.
    count_rows = (
        await session.execute(
            select(Run.eval_set_id, func.count())
            .where(Run.eval_set_id.in_(set_ids))
            .group_by(Run.eval_set_id)
        )
    ).all()
    run_counts = {es_id: n for es_id, n in count_rows}

    # 2. The most recent TREND_RUNS runs per set, newest first. The window
    #    function keeps this one query no matter how much history exists.
    ranked = (
        select(
            Run.id,
            Run.eval_set_id,
            Run.pass_rate,
            func.row_number()
            .over(partition_by=Run.eval_set_id, order_by=Run.started_at.desc())
            .label("rn"),
        )
        .where(Run.eval_set_id.in_(set_ids))
        .subquery()
    )
    recent_rows = (
        await session.execute(
            select(ranked.c.id, ranked.c.eval_set_id, ranked.c.pass_rate, ranked.c.rn)
            .where(ranked.c.rn <= TREND_RUNS)
            .order_by(ranked.c.eval_set_id, ranked.c.rn)
        )
    ).all()

    recent_by_set: dict[uuid.UUID, list] = {}
    for row in recent_rows:
        recent_by_set.setdefault(row.eval_set_id, []).append(row)

    # 3. Regression needs only the latest two runs per set — regression_summary
    #    reads runs_newest_first[0:2] and nothing else. Restricting the verdict
    #    load to those is what removes the bulk of the old row volume.
    regression_run_ids = [
        row.id for rows in recent_by_set.values() for row in rows[:2]
    ]
    verdicts_by_run: dict[uuid.UUID, dict] = {}
    if regression_run_ids:
        verdict_rows = (
            await session.execute(
                select(
                    QuestionResult.run_id,
                    QuestionResult.question_pk,
                    QuestionResult.verdict,
                ).where(QuestionResult.run_id.in_(regression_run_ids))
            )
        ).all()
        for run_id, qpk, verdict in verdict_rows:
            if verdict is not None:
                verdicts_by_run.setdefault(run_id, {})[qpk] = verdict

    # 4. Share lists.
    role_rows = (
        await session.execute(
            select(EvalSetRole.eval_set_id, EvalSetRole.user_subject, EvalSetRole.role)
            .where(EvalSetRole.eval_set_id.in_(set_ids))
            .order_by(EvalSetRole.role.asc(), EvalSetRole.user_subject.asc())
        )
    ).all()
    roles_by_set: dict[uuid.UUID, list[ShareEntry]] = {}
    for es_id, user_subject, role in role_rows:
        roles_by_set.setdefault(es_id, []).append(
            ShareEntry(subject=user_subject, role=role)
        )

    cards: list[EvalSetCard] = []
    for es in sets:
        recent = recent_by_set.get(es.id, [])  # newest first
        # The sparkline reads left-to-right as oldest-to-newest.
        trend = [
            float(r.pass_rate) if r.pass_rate is not None else None
            for r in reversed(recent)
        ]
        latest = next(
            (float(r.pass_rate) for r in recent if r.pass_rate is not None), None
        )
        reg = regression_summary(
            [
                RunVerdicts(
                    run_id=r.id,
                    started_at=None,  # ordering already fixed by the query
                    verdicts=verdicts_by_run.get(r.id, {}),
                )
                for r in recent[:2]
            ]
        )
        roles = roles_by_set.get(es.id, [])
        cards.append(
            EvalSetCard(
                id=es.id, name=es.name, description=es.description, metadata=es.meta,
                version=es.version, created_at=es.created_at, updated_at=es.updated_at,
                run_count=run_counts.get(es.id, 0),
                latest_pass_rate=latest, trend=trend,
                regressed=reg["regressed"], improved=reg["improved"],
                my_role=next((r.role for r in roles if r.subject == subject), None),
                roles=roles,
            )
        )
    return cards


async def _build_card(session: AsyncSession, es: EvalSet, subject: str) -> EvalSetCard:
    """Single-set convenience wrapper over `_build_cards`."""
    return (await _build_cards(session, [es], subject))[0]


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


@router.get("", response_model=EvalSetPage)
async def list_eval_sets(
    q: str | None = Query(None, description="case-insensitive name substring"),
    metadata_key: str | None = Query(None),
    metadata_value: str | None = Query(None),
    sort: str = Query("created_at", pattern="^(created_at|name)$"),
    limit: int = Query(24, ge=1, le=100),
    offset: int = Query(0, ge=0),
    subject: str = Depends(current_subject),
    session: AsyncSession = Depends(get_session),
):
    """A page of cards for the eval sets the caller has a role on (§6.13 top tier).

    Search, metadata filter and sort are applied in SQL rather than over the
    loaded page — filtering only what happens to be on screen would make "find
    my set" depend on how far the developer had scrolled, which defeats the
    point of having a filter at all (§6.10).
    """
    base = (
        select(EvalSet)
        .join(EvalSetRole, EvalSetRole.eval_set_id == EvalSet.id)
        .where(EvalSetRole.user_subject == subject)
    )
    if q:
        base = base.where(EvalSet.name.ilike(f"%{q}%"))
    if metadata_key:
        if metadata_value:
            # ->> yields text, so this compares the value as the string the
            # upload stored (§6.14: Stage 1 treats metadata values as strings).
            base = base.where(EvalSet.meta[metadata_key].astext == metadata_value)
        else:
            base = base.where(EvalSet.meta.has_key(metadata_key))  # noqa: W601

    total = await session.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0

    order = EvalSet.name.asc() if sort == "name" else EvalSet.created_at.desc()
    # id as a tiebreaker: without it, sets sharing a created_at/name can swap
    # places between pages and be shown twice or not at all.
    sets = (
        await session.scalars(
            base.order_by(order, EvalSet.id.asc()).limit(limit).offset(offset)
        )
    ).all()

    items = await _build_cards(session, list(sets), subject)
    return EvalSetPage(
        items=items, total=total, has_more=offset + len(items) < total
    )


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
