"""Promoting playground work into an eval set (§10.8).

Two halves, tested apart because they fail for different reasons:

* **Synthesis** drafts an expected process from a trace. It is offered on a
  button and must never write itself onto the attempt — the draft describes what
  the agent *did*, and letting that quietly become what is *expected* is how the
  diagnosis ends up comparing every future run against a recording of one past
  one.
* **Creation** copies questions out of eval sets the caller can already read.
  That needs a real database (the copy, the permission check and the de-dup are
  all SQL), so those tests skip unless `TEST_DATABASE_URL` is set, matching
  test_pagination.py.
"""
from __future__ import annotations

import os
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import playground
from app.db import Base
from app.integrations import Seams
from app.integrations.base import Span, Trace
from app.integrations.fake import FakeSynthesisClient
from app.models import EvalSet, EvalSetRole, Question, QuestionSkill
from app.playground import PlaygroundAttempt
from app.routers import eval_sets as eval_sets_router
from app.routers import playground as playground_router
from app.schemas import EvalSetCreate, EvalSetFromShortlist

pytestmark_db = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="set TEST_DATABASE_URL to run the database-backed shortlist tests",
)
TEST_DB = os.environ.get("TEST_DATABASE_URL")


# --- Synthesis --------------------------------------------------------------

def make_attempt(subject="alice", **kwargs) -> PlaygroundAttempt:
    defaults = dict(
        id=uuid.uuid4(), subject=subject, question="how much did ACME owe?",
        ground_truth_response=None, ground_truth_reasoning=None,
        workspace=None, workspace_baseline=None, config={}, secrets={},
        correlation_id=uuid.uuid4().hex,
    )
    defaults.update(kwargs)
    return PlaygroundAttempt(**defaults)


@pytest.fixture(autouse=True)
def clean_store():
    playground.clear()
    yield
    playground.clear()


def trace_of(n=3) -> Trace:
    return Trace(
        correlation_id="c",
        spans=[
            Span(index=i, tool_name=f"tool_{i}", status="success",
                 input="in", output="out", token_usage={})
            for i in range(n)
        ],
    )


def seams_with(synthesis):
    return Seams(agent=None, judge=None, trace=None, diagnosis=None, synthesis=synthesis)


async def test_synthesis_drafts_from_the_trace(monkeypatch):
    attempt = make_attempt()
    attempt.trace = trace_of(3)
    attempt.agent_response = "ACME owed 12,480."
    playground.add(attempt)
    monkeypatch.setattr(
        playground_router, "build_seams", lambda *a, **k: seams_with(FakeSynthesisClient())
    )

    out = await playground_router.synthesize_reasoning(attempt.id, subject="alice")

    assert out.reasoning_process.startswith("1.")
    assert out.model_used == "fake-synthesis"


async def test_synthesis_does_not_write_itself_onto_the_attempt(monkeypatch):
    """The draft is a starting point, not a decision.

    Storing it would turn one observed run into the standard the next one is
    graded against, without anyone having agreed to that.
    """
    attempt = make_attempt()
    attempt.trace = trace_of(2)
    playground.add(attempt)
    monkeypatch.setattr(
        playground_router, "build_seams", lambda *a, **k: seams_with(FakeSynthesisClient())
    )

    await playground_router.synthesize_reasoning(attempt.id, subject="alice")

    assert attempt.ground_truth_reasoning is None
    assert attempt.diagnosable is False


async def test_synthesis_without_a_trace_is_a_409(monkeypatch):
    attempt = make_attempt()
    playground.add(attempt)

    with pytest.raises(HTTPException) as exc:
        await playground_router.synthesize_reasoning(attempt.id, subject="alice")
    assert exc.value.status_code == 409
    assert "no trace" in exc.value.detail


async def test_synthesis_reports_the_models_own_error(monkeypatch):
    class Boom:
        model_name = "boom-model"

        async def synthesize(self, trace, question, agent_response):
            raise RuntimeError("context length exceeded")

    attempt = make_attempt()
    attempt.trace = trace_of(1)
    playground.add(attempt)
    monkeypatch.setattr(playground_router, "build_seams", lambda *a, **k: seams_with(Boom()))

    with pytest.raises(HTTPException) as exc:
        await playground_router.synthesize_reasoning(attempt.id, subject="alice")
    assert exc.value.status_code == 502
    assert "context length exceeded" in exc.value.detail


async def test_an_empty_draft_is_a_failure_not_a_blank_field(monkeypatch):
    """A blank expected process would be accepted by the form and then be
    un-diagnosable forever; better to say the model produced nothing."""
    class Empty:
        model_name = "empty-model"

        async def synthesize(self, trace, question, agent_response):
            return "   "

    attempt = make_attempt()
    attempt.trace = trace_of(1)
    playground.add(attempt)
    monkeypatch.setattr(playground_router, "build_seams", lambda *a, **k: seams_with(Empty()))

    with pytest.raises(HTTPException) as exc:
        await playground_router.synthesize_reasoning(attempt.id, subject="alice")
    assert exc.value.status_code == 502


async def test_another_subjects_attempt_cannot_be_synthesized():
    attempt = make_attempt(subject="bob")
    attempt.trace = trace_of(1)
    playground.add(attempt)

    with pytest.raises(HTTPException) as exc:
        await playground_router.synthesize_reasoning(attempt.id, subject="alice")
    assert exc.value.status_code == 404


# --- Creating the eval set --------------------------------------------------

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


async def make_set(session, name, questions, subject="alice", skills=None):
    es = EvalSet(name=name, source_format="jsonl", meta={})
    session.add(es)
    await session.flush()
    session.add(EvalSetRole(eval_set_id=es.id, user_subject=subject, role="owner"))
    for i, text_ in enumerate(questions):
        q = Question(
            eval_set_id=es.id, question_id=f"q_{name}_{i}", question=text_,
            ground_truth_response=f"gt {i}", ground_truth_reasoning=f"steps {i}",
        )
        session.add(q)
        await session.flush()
        for ordinal, skill in enumerate((skills or {}).get(text_, [])):
            session.add(QuestionSkill(question_pk=q.id, skill_name=skill, ordinal=ordinal))
    await session.commit()
    return es


def body(**kwargs) -> EvalSetFromShortlist:
    payload = dict(name="promoted", questions=[], include_eval_set_ids=[])
    payload.update(kwargs)
    return EvalSetFromShortlist(**payload)


def shortlisted(question, skills=None):
    return {
        "question": question,
        "ground_truth_response": f"answer to {question}",
        "ground_truth_reasoning": "1. Read the skill.\n2. Answered.",
        "skills": skills or [],
    }


async def questions_of(session, set_id):
    rows = (
        await session.execute(
            Question.__table__.select().where(Question.eval_set_id == set_id)
        )
    ).all()
    return rows


@pytestmark_db
async def test_creates_a_set_from_shortlisted_questions_alone(session):
    out = await eval_sets_router.create_eval_set_from_shortlist(
        body(questions=[shortlisted("new question")]), subject="alice", session=session
    )

    assert out.question_count == 1
    rows = await questions_of(session, out.id)
    assert len(rows) == 1
    # A promoted question is a new question: it gets a fresh generated id.
    assert rows[0].question_id.startswith("q_")


@pytestmark_db
async def test_includes_questions_copied_from_an_existing_set(session):
    source = await make_set(session, "old", ["first", "second"])

    out = await eval_sets_router.create_eval_set_from_shortlist(
        body(questions=[shortlisted("new question")], include_eval_set_ids=[source.id]),
        subject="alice", session=session,
    )

    assert out.question_count == 3
    rows = await questions_of(session, out.id)
    assert {r.question for r in rows} == {"new question", "first", "second"}
    # The source set is untouched — this copies, it never moves.
    assert len(await questions_of(session, source.id)) == 2


@pytestmark_db
async def test_copied_questions_get_new_ids(session):
    """Two rows claiming to be the same question_id would drift apart the first
    time either set's copy is edited."""
    source = await make_set(session, "old", ["first"])
    old = (await questions_of(session, source.id))[0]

    out = await eval_sets_router.create_eval_set_from_shortlist(
        body(include_eval_set_ids=[source.id]), subject="alice", session=session
    )

    new = (await questions_of(session, out.id))[0]
    assert new.question_id != old.question_id
    assert new.question == old.question
    assert new.ground_truth_reasoning == old.ground_truth_reasoning


@pytestmark_db
async def test_skill_tags_are_copied(session):
    source = await make_set(
        session, "old", ["billing question"], skills={"billing question": ["billing"]}
    )

    out = await eval_sets_router.create_eval_set_from_shortlist(
        body(include_eval_set_ids=[source.id]), subject="alice", session=session
    )

    new = (await questions_of(session, out.id))[0]
    tags = (
        await session.execute(
            QuestionSkill.__table__.select().where(QuestionSkill.question_pk == new.id)
        )
    ).all()
    assert [t.skill_name for t in tags] == ["billing"]


@pytestmark_db
async def test_duplicate_question_text_is_skipped_and_counted(session):
    """Two included sets usually share history; asking the same question twice in
    one run is cost without information. The count is reported, not swallowed."""
    a = await make_set(session, "a", ["shared", "only-in-a"])
    b = await make_set(session, "b", ["shared", "only-in-b"])

    out = await eval_sets_router.create_eval_set_from_shortlist(
        body(include_eval_set_ids=[a.id, b.id]), subject="alice", session=session
    )

    assert out.question_count == 3
    assert out.duplicates_skipped == 1


@pytestmark_db
async def test_a_shortlisted_question_wins_over_a_copy_of_the_same_text(session):
    """The edited version is the one the developer came here to create."""
    source = await make_set(session, "old", ["same question"])

    out = await eval_sets_router.create_eval_set_from_shortlist(
        body(questions=[shortlisted("same question")], include_eval_set_ids=[source.id]),
        subject="alice", session=session,
    )

    rows = await questions_of(session, out.id)
    assert len(rows) == 1
    assert rows[0].ground_truth_response == "answer to same question"


@pytestmark_db
async def test_an_unreadable_eval_set_is_a_404_and_creates_nothing(session):
    source = await make_set(session, "bobs", ["hidden"], subject="bob")

    with pytest.raises(HTTPException) as exc:
        await eval_sets_router.create_eval_set_from_shortlist(
            body(questions=[shortlisted("mine")], include_eval_set_ids=[source.id]),
            subject="alice", session=session,
        )
    assert exc.value.status_code == 404

    # Checked before anything is written, so a refusal leaves no half-made set.
    remaining = (await session.execute(EvalSet.__table__.select())).all()
    assert [r.name for r in remaining] == ["bobs"]


@pytestmark_db
async def test_creating_an_empty_set_is_refused(session):
    with pytest.raises(HTTPException) as exc:
        await eval_sets_router.create_eval_set_from_shortlist(
            body(), subject="alice", session=session
        )
    assert exc.value.status_code == 422


@pytestmark_db
async def test_creator_is_owner_and_shares_apply(session):
    out = await eval_sets_router.create_eval_set_from_shortlist(
        body(questions=[shortlisted("q")], shares=[{"subject": "bob", "role": "viewer"}]),
        subject="alice", session=session,
    )

    roles = (
        await session.execute(
            EvalSetRole.__table__.select().where(EvalSetRole.eval_set_id == out.id)
        )
    ).all()
    assert {r.user_subject: r.role for r in roles} == {"alice": "owner", "bob": "viewer"}


@pytestmark_db
async def test_the_new_set_reports_the_creators_role_straight_away(session):
    """The promoted set must answer "what am I here?" on its very first read.

    This is the payload the UI gates its owner-only controls on. It used to read
    a role map fetched once when the page loaded, so a set created *during* the
    session was missing from it entirely and its owner got no Edit questions
    button — every time, since promoting a shortlist navigates straight into the
    set it just created.
    """
    out = await eval_sets_router.create_eval_set_from_shortlist(
        body(questions=[shortlisted("q")], shares=[{"subject": "bob", "role": "viewer"}]),
        subject="alice", session=session,
    )

    card = await eval_sets_router.get_eval_set(out.id, subject="alice", session=session)
    assert card.my_role == "owner"

    shared = await eval_sets_router.get_eval_set(out.id, subject="bob", session=session)
    assert shared.my_role == "viewer"


@pytestmark_db
async def test_an_uploaded_set_reports_its_creators_role_too(session):
    """The same guarantee on the other creation path, so the two cannot drift."""
    created = await eval_sets_router.create_eval_set(
        EvalSetCreate(
            name="uploaded",
            jsonl='{"question": "q", "ground_truth_response": "a",'
                  ' "ground_truth_reasoning_process_description": "1. did it",'
                  ' "skill": ["billing"]}',
            source_format="jsonl",
        ),
        subject="alice", session=session,
    )

    card = await eval_sets_router.get_eval_set(
        uuid.UUID(created["id"]), subject="alice", session=session
    )
    assert card.my_role == "owner"


@pytestmark_db
async def test_a_stranger_cannot_read_the_promoted_set(session):
    """`my_role` is only ever the caller's own role — the guard is what stops a
    set being readable at all, and it has to keep doing that."""
    out = await eval_sets_router.create_eval_set_from_shortlist(
        body(questions=[shortlisted("q")]), subject="alice", session=session
    )

    with pytest.raises(HTTPException) as exc:
        await eval_sets_router.require_reader(out.id, subject="carol", session=session)
    assert exc.value.status_code == 403
