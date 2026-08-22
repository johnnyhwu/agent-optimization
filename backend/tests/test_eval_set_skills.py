"""The skills an eval set's questions are tagged with.

Served so the "Run eval" dialog can hold this set's tags against the skills the
target agent actually has, and say so before a run is started rather than after
every question tagged `billling` has come back wrong for a reason nobody can see
in the trace.

Its own endpoint rather than a client-side pass over `GET .../questions`, which
would pull every question's text and ground truth across the wire to read a
handful of tags. Database-backed, so it skips unless `TEST_DATABASE_URL` is set,
matching test_judge_prompt.py and test_pagination.py.
"""
from __future__ import annotations

import os

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import require_reader
from app.db import Base
from app.models import EvalSet, EvalSetRole, Question, QuestionSkill
from app.routers import eval_sets as eval_sets_router

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="set TEST_DATABASE_URL to run the database-backed eval-set skill tests",
)
TEST_DB = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
async def engine():
    eng = create_async_engine(TEST_DB)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine):
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        await s.execute(
            text(
                "TRUNCATE span_analyses, question_results, question_skills,"
                " questions, runs, eval_set_roles, eval_sets CASCADE"
            )
        )
        await s.commit()
        yield s


async def make_set(session, tagging: dict[str, list[str]], owner="alice", viewer="bob"):
    """An eval set whose questions carry the given tags: {question_id: [skills]}."""
    es = EvalSet(name="billing suite", source_format="jsonl", meta={})
    session.add(es)
    await session.flush()
    session.add(EvalSetRole(eval_set_id=es.id, user_subject=owner, role="owner"))
    if viewer:
        session.add(EvalSetRole(eval_set_id=es.id, user_subject=viewer, role="viewer"))
    for qid, skills in tagging.items():
        q = Question(
            eval_set_id=es.id, question_id=qid, question="q?",
            ground_truth_response="a", ground_truth_reasoning="r",
        )
        session.add(q)
        await session.flush()
        for ordinal, skill in enumerate(skills):
            session.add(
                QuestionSkill(question_pk=q.id, skill_name=skill, ordinal=ordinal)
            )
    await session.commit()
    return es


async def test_each_skill_is_named_once_with_the_questions_behind_it(session):
    es = await make_set(
        session,
        {
            "q_1": ["billing"],
            "q_2": ["billing"],
            "q_3": ["reporting"],
        },
    )

    out = await eval_sets_router.list_eval_set_skills(
        eval_set_id=es.id, subject="alice", session=session
    )

    assert [(s.skill_name, s.question_count) for s in out.skills] == [
        ("billing", 2),
        ("reporting", 1),
    ]


async def test_a_question_with_two_tags_counts_under_both(session):
    """Unlike the optimizer's grouping, which keeps only single-tagged questions.

    The question here is "does the agent have this skill", and a question
    depending on two skills depends on both of them being there.
    """
    es = await make_set(session, {"q_1": ["billing", "reporting"]})

    out = await eval_sets_router.list_eval_set_skills(
        eval_set_id=es.id, subject="alice", session=session
    )

    assert [s.skill_name for s in out.skills] == ["billing", "reporting"]
    assert all(s.question_count == 1 for s in out.skills)


async def test_questions_with_no_tag_are_counted_but_not_named(session):
    """The dialog says "another N questions have no skill tag" beside the
    coverage warning, and it cannot say it without this number. They are not a
    skill called "" — they are questions the check has nothing to check."""
    es = await make_set(session, {"q_1": ["billing"], "q_2": [], "q_3": []})

    out = await eval_sets_router.list_eval_set_skills(
        eval_set_id=es.id, subject="alice", session=session
    )

    assert [s.skill_name for s in out.skills] == ["billing"]
    assert out.untagged_question_count == 2


async def test_a_viewer_may_read_them(session):
    """Anyone who may run this set may see why the run is about to be warned
    about. Refusing a viewer would leave them looking at a warning they cannot
    account for."""
    es = await make_set(session, {"q_1": ["billing"]})

    out = await eval_sets_router.list_eval_set_skills(
        eval_set_id=es.id, subject="bob", session=session
    )
    assert [s.skill_name for s in out.skills] == ["billing"]


async def test_a_stranger_may_not(session):
    """§6.16 keeps authorization in two shared dependencies rather than in the
    handlers, and this endpoint declares `require_reader` like its neighbours —
    so "a stranger cannot read them" is exactly the statement that the
    dependency refuses them, with no new rule of its own to test."""
    es = await make_set(session, {"q_1": ["billing"]}, viewer=None)

    assert await require_reader(es.id, "alice", session) == "alice"
    with pytest.raises(HTTPException) as caught:
        await require_reader(es.id, "mallory", session)
    assert caught.value.status_code in (403, 404)
