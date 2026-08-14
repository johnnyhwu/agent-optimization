"""Editing a question's skill tags (§6.11 locked set: edit only).

Two halves, split the way the rest of this suite splits them: the naming rule is
pure and runs everywhere, the endpoint needs real SQL — the tag table is keyed by
(question_pk, ordinal) and the edit is a delete-then-insert inside the same
transaction as the version-checked UPDATE — so those tests skip unless
`TEST_DATABASE_URL` is set, matching test_shortlist.py and test_pagination.py.
"""
from __future__ import annotations

import os
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models import EvalSet, EvalSetRole, Question, QuestionSkill
from app.routers import questions as questions_router
from app.schemas import QuestionUpdate
from app.services.upload import normalize_skills

pytestmark_db = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="set TEST_DATABASE_URL to run the database-backed question edit tests",
)
TEST_DB = os.environ.get("TEST_DATABASE_URL")


# --- The naming rule --------------------------------------------------------

def test_names_are_stripped_and_blanks_dropped():
    # What a text box produces: "billing, reports," typed with a trailing comma
    # and a stray space.
    assert normalize_skills([" billing ", "reports", "", "   "]) == ["billing", "reports"]


def test_duplicates_collapse_keeping_the_first():
    # Not tidiness. The optimizer groups questions with *exactly one* skill and
    # sends the rest to `ambiguous`, so a doubled name would drop the question
    # out of the group it plainly belongs to.
    assert normalize_skills(["billing", "billing"]) == ["billing"]
    assert normalize_skills(["reports", "billing", "reports"]) == ["reports", "billing"]


def test_case_is_preserved_and_not_collapsed():
    # A skill name is matched against the agent's skill directory name, so these
    # are two names, not one written twice.
    assert normalize_skills(["Billing", "billing"]) == ["Billing", "billing"]


def test_order_is_the_order_given():
    assert normalize_skills(["reports", "billing"]) == ["reports", "billing"]


# --- The endpoint -----------------------------------------------------------

@pytest.fixture
async def session():
    engine = create_async_engine(TEST_DB, future=True)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def make_question(session, skills=("billing",), subject="alice"):
    es = EvalSet(name=f"set-{uuid.uuid4().hex[:6]}", source_format="jsonl")
    session.add(es)
    await session.flush()
    session.add(EvalSetRole(eval_set_id=es.id, user_subject=subject, role="owner"))
    q = Question(
        eval_set_id=es.id, question_id="q_test01", question="how much did ACME owe?",
        ground_truth_response="1,200", ground_truth_reasoning="read billing, sum",
    )
    session.add(q)
    await session.flush()
    for ordinal, skill in enumerate(skills):
        session.add(QuestionSkill(question_pk=q.id, skill_name=skill, ordinal=ordinal))
    await session.commit()
    # Plain values, not the ORM objects: these tests compare "before" against
    # "after", and a live `Question` is refreshed by the same session the
    # endpoint writes through — `q.version` read afterwards is the *new* version,
    # which quietly turns every such assertion into `x == x`.
    return es.id, q.id, q.version


async def tags_of(session, question_pk):
    return list(
        await session.scalars(
            select(QuestionSkill.skill_name)
            .where(QuestionSkill.question_pk == question_pk)
            .order_by(QuestionSkill.ordinal.asc())
        )
    )


@pytestmark_db
async def test_tags_are_replaced_wholesale(session):
    es_id, q_pk, version = await make_question(session, skills=("billing", "reports"))
    out = await questions_router.update_question(
        es_id, q_pk, QuestionUpdate(skills=["invoices"], version=version),
        subject="alice", session=session,
    )
    assert out.skills == ["invoices"]
    assert await tags_of(session, q_pk) == ["invoices"]
    # The tags are part of the question, so editing them bumps the version the
    # next save has to assert.
    assert out.version == version + 1


@pytestmark_db
async def test_omitting_skills_leaves_the_tags_alone(session):
    es_id, q_pk, version = await make_question(session, skills=("billing",))
    out = await questions_router.update_question(
        es_id, q_pk, QuestionUpdate(question="rewritten", version=version),
        subject="alice", session=session,
    )
    assert out.skills == ["billing"]
    assert out.question == "rewritten"


@pytestmark_db
async def test_an_empty_list_clears_the_tags(session):
    # Distinct from omitting the field. An untagged question is a state the
    # product already has (a set promoted from a shortlist arrives with none),
    # and the editor says what it costs: no skill group claims the question.
    es_id, q_pk, version = await make_question(session, skills=("billing",))
    out = await questions_router.update_question(
        es_id, q_pk, QuestionUpdate(skills=[], version=version),
        subject="alice", session=session,
    )
    assert out.skills == []
    assert await tags_of(session, q_pk) == []


@pytestmark_db
async def test_a_stale_version_changes_no_tags(session):
    es_id, q_pk, version = await make_question(session, skills=("billing",))
    with pytest.raises(HTTPException) as exc:
        await questions_router.update_question(
            es_id, q_pk, QuestionUpdate(skills=["invoices"], version=version + 5),
            subject="alice", session=session,
        )
    assert exc.value.status_code == 409
    # The delete-then-insert runs after the version check and shares its
    # transaction, so losing the race must leave the old tags standing.
    await session.rollback()
    assert await tags_of(session, q_pk) == ["billing"]
