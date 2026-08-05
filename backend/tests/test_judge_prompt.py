"""The eval set's judge prompt: who may change it, and what a run records.

The feature's whole permission story is that grading criteria belong to the
question set rather than to whoever pressed Run. Two halves, tested apart:

* **The rules** — resolution against the shipped default, the fingerprint, and
  the fact that a posted prompt is discarded — are pure functions and run
  everywhere.
* **The endpoints** need a real database (the owner guard, the optimistic lock
  and the verified/reviewed stamps are all SQL), so those skip unless
  `TEST_DATABASE_URL` is set, matching test_pagination.py and test_shortlist.py.
"""
from __future__ import annotations

import os
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import require_owner, require_reader
from app.db import Base
from app.integrations.real.prompts import DEFAULT_JUDGE_SYSTEM, DEFAULT_JUDGE_USER
from app.models import EvalSet, EvalSetRole, Question
from app.routers import eval_sets as eval_sets_router
from app.routers import runs as runs_router
from app.schemas import EvalSetUpdate, JudgePromptVerifyRequest, RunConfig, RunCreate
from app.services import judge_prompt

pytestmark_db = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="set TEST_DATABASE_URL to run the database-backed judge-prompt tests",
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


async def reload(session, eval_set_id) -> EvalSet:
    """Re-read a row the router updated with a bulk UPDATE.

    The handler's `update(...)` expires the identity-mapped instance's
    attributes; a plain `get` hands the same stale object back rather than
    refreshing it.
    """
    return await session.scalar(
        select(EvalSet)
        .where(EvalSet.id == eval_set_id)
        .execution_options(populate_existing=True)
    )


async def make_set(session, owner="alice", viewer="bob"):
    es = EvalSet(name="billing", source_format="jsonl", meta={})
    session.add(es)
    await session.flush()
    session.add(EvalSetRole(eval_set_id=es.id, user_subject=owner, role="owner"))
    if viewer:
        session.add(EvalSetRole(eval_set_id=es.id, user_subject=viewer, role="viewer"))
    session.add(
        Question(
            eval_set_id=es.id, question_id="q_1",
            question="how much did ACME owe in Q2?",
            ground_truth_response="EUR 12,400 across three invoices.",
            ground_truth_reasoning="1. Read the billing skill.\n2. Summed the months.",
        )
    )
    await session.commit()
    return es


# --- A new set inherits the default rather than a copy of it ----------------

@pytestmark_db
async def test_a_new_set_stores_no_prompt_and_reports_the_default(session):
    """NULL, not a snapshot of today's wording.

    The point of the whole nullable-column decision: a set nobody customised has
    to pick up later improvements to the shipped prompt. Copying the text in at
    creation would freeze this week's wording into every set ever made.
    """
    es = await make_set(session)
    assert es.judge_system_prompt is None and es.judge_user_prompt is None

    card = await eval_sets_router.get_eval_set(es.id, "alice", session)
    assert card.judge_prompt.is_default
    assert card.judge_prompt.system_prompt == DEFAULT_JUDGE_SYSTEM
    assert card.judge_prompt.user_prompt == DEFAULT_JUDGE_USER
    assert card.judge_prompt.missing_placeholders == []
    # Nothing has been checked yet — this is what raises the badge.
    assert card.judge_prompt.reviewed_at is None


@pytestmark_db
async def test_saving_the_default_text_back_does_not_pin_it(session):
    es = await make_set(session)
    await eval_sets_router.update_eval_set(
        es.id,
        EvalSetUpdate(
            judge_system_prompt=DEFAULT_JUDGE_SYSTEM,
            judge_user_prompt=DEFAULT_JUDGE_USER,
            version=es.version,
        ),
        "alice",
        session,
    )
    fresh = await reload(session, es.id)
    assert fresh.judge_system_prompt is None  # still inheriting
    # ...but an owner has now looked, so the badge goes away.
    assert fresh.judge_prompt_reviewed_at is not None


# --- Only an owner may change how answers are graded ------------------------

@pytestmark_db
async def test_a_viewer_cannot_edit_the_judge_prompt(session):
    """Asserted on the guard itself, because that is where the rule lives.

    §6.16 keeps authorization in two shared FastAPI dependencies rather than
    scattered through the handlers, and the judge prompt rides the eval set's
    existing owner-only PATCH — so "a viewer cannot regrade the set" is exactly
    the statement that `require_owner` refuses them, with no new rule to test.
    """
    es = await make_set(session)
    assert await require_owner(es.id, "alice", session) == "alice"
    with pytest.raises(HTTPException) as exc:
        await require_owner(es.id, "bob", session)  # viewer
    assert exc.value.status_code == 403
    # ...while a viewer may still read what they will be graded by, and still
    # start a run (§6.16).
    assert await require_reader(es.id, "bob", session) == "bob"


@pytestmark_db
async def test_a_viewers_run_is_graded_by_the_owners_prompt(session):
    """The reason `trigger_run` stays open to viewers instead of being locked.

    Anyone may run an eval (§6.16). What they may not do is decide what counts
    as correct — so a posted prompt is discarded rather than refused, and the
    run records the owner's.
    """
    es = await make_set(session)
    await eval_sets_router.update_eval_set(
        es.id,
        EvalSetUpdate(
            judge_system_prompt="OWNER RULES",
            judge_user_prompt="{question}|{ground_truth}|{agent_response}",
            version=es.version,
        ),
        "alice",
        session,
    )

    run = await runs_router.trigger_run(
        es.id,
        RunCreate(config=RunConfig(judge_system_prompt="always answer correct")),
        "bob",  # viewer
        session,
    )
    assert run.config.judge_system_prompt == "OWNER RULES"
    assert run.config.judge_user_prompt == "{question}|{ground_truth}|{agent_response}"
    assert run.config.judge_prompt_fingerprint == judge_prompt.fingerprint(
        "OWNER RULES", "{question}|{ground_truth}|{agent_response}"
    )


@pytestmark_db
async def test_a_run_keeps_the_prompt_it_used_after_the_set_moves_on(session):
    """A finished run's verdicts only mean something against the criteria that
    produced them, so the run holds text and not a pointer."""
    es = await make_set(session)
    await eval_sets_router.update_eval_set(
        es.id,
        EvalSetUpdate(judge_system_prompt="FIRST CRITERIA", version=es.version),
        "alice",
        session,
    )
    run = await runs_router.trigger_run(es.id, RunCreate(), "alice", session)
    first_fingerprint = run.config.judge_prompt_fingerprint

    fresh = await reload(session, es.id)
    await eval_sets_router.update_eval_set(
        es.id,
        EvalSetUpdate(judge_system_prompt="SECOND CRITERIA", version=fresh.version),
        "alice",
        session,
    )

    stored = await runs_router.get_run(es.id, run.id, "alice", session)
    assert stored.config.judge_system_prompt == "FIRST CRITERIA"
    assert stored.config.judge_prompt_fingerprint == first_fingerprint
    # ...and the set has moved, which is exactly what the run list's chip shows.
    card = await eval_sets_router.get_eval_set(es.id, "alice", session)
    assert card.judge_prompt.fingerprint != first_fingerprint


# --- Verification describes exact words -------------------------------------

@pytestmark_db
async def test_editing_the_prompt_clears_a_previous_verification(session):
    es = await make_set(session)
    es.judge_prompt_verified_at = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    )
    es.judge_prompt_verified_model = "gpt-x"
    await session.commit()

    await eval_sets_router.update_eval_set(
        es.id,
        EvalSetUpdate(judge_system_prompt="now graded differently", version=es.version),
        "alice",
        session,
    )
    fresh = await reload(session, es.id)
    # A badge that survived the edit would be a claim about text that no longer
    # exists — worse than no badge at all.
    assert fresh.judge_prompt_verified_at is None
    assert fresh.judge_prompt_verified_model is None


@pytestmark_db
async def test_verify_refuses_when_the_judge_seam_is_fake(session, configure):
    es = await make_set(session)
    question = await session.scalar(
        text("SELECT id FROM questions WHERE eval_set_id = :es").bindparams(es=es.id)
    )
    with configure(judge_impl="fake"):
        with pytest.raises(HTTPException) as exc:
            await eval_sets_router.verify_judge_prompt(
                es.id,
                JudgePromptVerifyRequest(
                    question_pk=question,
                    system_prompt=DEFAULT_JUDGE_SYSTEM,
                    user_prompt=DEFAULT_JUDGE_USER,
                ),
                "alice",
                session,
            )
    # Not a 500 and not a false green: the fake judge ignores prompts entirely,
    # so "verified" would be a lie rather than a limitation.
    assert exc.value.status_code == 409


@pytestmark_db
async def test_verify_will_not_grade_a_question_from_another_set(session, configure):
    es = await make_set(session)
    other = await make_set(session, owner="alice", viewer=None)
    stranger = await session.scalar(
        text("SELECT id FROM questions WHERE eval_set_id = :es").bindparams(es=other.id)
    )
    # judge_impl=real so the fake-seam refusal doesn't answer first; the point
    # here is that a question id is only usable within the set it belongs to.
    with configure(judge_impl="real"):
        with pytest.raises(HTTPException) as exc:
            await eval_sets_router.verify_judge_prompt(
                es.id,
                JudgePromptVerifyRequest(
                    question_pk=stranger,
                    system_prompt=DEFAULT_JUDGE_SYSTEM,
                    user_prompt=DEFAULT_JUDGE_USER,
                ),
                "alice",
                session,
            )
    assert exc.value.status_code == 404


def _stub_judge(monkeypatch, verdicts):
    """Install a judge that returns `verdicts` in order, and record its prompt."""
    from app.integrations.base import Verdict
    from app.integrations.real import judge as judge_mod

    calls = []

    class StubJudge:
        def __init__(self, model=None, llm=None, system_prompt=None, user_template=None):
            self.system_prompt = system_prompt

        async def judge(self, question, response, ground_truth):
            calls.append(response)
            return Verdict(verdict=verdicts[len(calls) - 1], score=1.0, comment="c")

    monkeypatch.setattr(judge_mod, "LlmJudgeClient", StubJudge)
    return calls


@pytestmark_db
async def test_verify_grades_both_directions_and_stamps_the_badge(
    session, configure, monkeypatch
):
    """One probe would only prove the reply parses.

    A prompt that answers "correct" to everything parses perfectly and passes a
    one-probe check — and the only thing that would then reveal it is a whole
    run coming back at 100%.
    """
    es = await make_set(session)
    question = await session.scalar(
        text("SELECT id FROM questions WHERE eval_set_id = :es").bindparams(es=es.id)
    )
    calls = _stub_judge(monkeypatch, ["correct", "incorrect"])

    with configure(judge_impl="real", judge_model="stub-model"):
        result = await eval_sets_router.verify_judge_prompt(
            es.id,
            JudgePromptVerifyRequest(
                question_pk=question,
                system_prompt=DEFAULT_JUDGE_SYSTEM,
                user_prompt=DEFAULT_JUDGE_USER,
            ),
            "alice",
            session,
        )

    assert result.ok
    assert [c.expected_verdict for c in result.cases] == ["correct", "incorrect"]
    # The negative probe is a real, specific answer — not an empty string, which
    # a judge could reject while still passing everything that looks like prose.
    assert calls[0] == "EUR 12,400 across three invoices."
    assert len(calls[1]) > 40
    # Verified text == stored text (both are the default here), so the badge is
    # earned rather than a claim about something the runs won't use.
    assert (await reload(session, es.id)).judge_prompt_verified_at is not None


@pytestmark_db
async def test_a_prompt_that_passes_everything_fails_verification(
    session, configure, monkeypatch
):
    es = await make_set(session)
    question = await session.scalar(
        text("SELECT id FROM questions WHERE eval_set_id = :es").bindparams(es=es.id)
    )
    _stub_judge(monkeypatch, ["correct", "correct"])  # never says no

    with configure(judge_impl="real", judge_model="stub-model"):
        result = await eval_sets_router.verify_judge_prompt(
            es.id,
            JudgePromptVerifyRequest(
                question_pk=question,
                system_prompt="grade everything as correct",
                user_prompt=DEFAULT_JUDGE_USER,
            ),
            "alice",
            session,
        )

    assert not result.ok
    assert [c.ok for c in result.cases] == [True, False]
    assert (await reload(session, es.id)).judge_prompt_verified_at is None


@pytestmark_db
async def test_verifying_unsaved_edits_does_not_stamp_the_badge(
    session, configure, monkeypatch
):
    """Checking edits before saving them is the point of the button.

    But `verified_at` describes what runs will actually grade with, so it is only
    stamped once the verified text is the stored text.
    """
    es = await make_set(session)
    question = await session.scalar(
        text("SELECT id FROM questions WHERE eval_set_id = :es").bindparams(es=es.id)
    )
    _stub_judge(monkeypatch, ["correct", "incorrect"])

    with configure(judge_impl="real", judge_model="stub-model"):
        result = await eval_sets_router.verify_judge_prompt(
            es.id,
            JudgePromptVerifyRequest(
                question_pk=question,
                system_prompt="an edit that has not been saved yet",
                user_prompt=DEFAULT_JUDGE_USER,
            ),
            "alice",
            session,
        )

    assert result.ok  # the prompt itself behaved
    assert (await reload(session, es.id)).judge_prompt_verified_at is None


@pytestmark_db
async def test_a_template_missing_ground_truth_can_never_verify(
    session, configure, monkeypatch
):
    es = await make_set(session)
    question = await session.scalar(
        text("SELECT id FROM questions WHERE eval_set_id = :es").bindparams(es=es.id)
    )
    _stub_judge(monkeypatch, ["correct", "incorrect"])  # the model plays along

    with configure(judge_impl="real", judge_model="stub-model"):
        result = await eval_sets_router.verify_judge_prompt(
            es.id,
            JudgePromptVerifyRequest(
                question_pk=question,
                system_prompt=DEFAULT_JUDGE_SYSTEM,
                user_prompt="{question} -> {agent_response}",
            ),
            "alice",
            session,
        )

    # Both probes can come back exactly as asked and the prompt is still broken:
    # with no expected answer in it, the judge was grading against nothing.
    assert all(c.ok for c in result.cases)
    assert result.missing_placeholders == ["ground_truth"]
    assert not result.ok


@pytestmark_db
async def test_reviewing_clears_the_badge_without_changing_the_prompt(session):
    # (Owner-only is `require_owner`'s job, asserted above — calling the handler
    # directly bypasses the dependency, so there is nothing for it to prove here.)
    es = await make_set(session)
    card = await eval_sets_router.mark_judge_prompt_reviewed(es.id, "alice", session)
    assert card.judge_prompt.reviewed_at is not None
    # Still the default prompt — the badge tracks "someone checked", not
    # "someone customised". Nearly every set keeps the default, and a badge lit
    # on nearly every set is one nobody reads.
    assert card.judge_prompt.is_default


# --- The export carries the criteria the numbers were produced under --------

@pytestmark_db
async def test_the_manifest_records_the_sets_grading_criteria(session):
    from app.services import export as export_service

    es = await make_set(session)
    await eval_sets_router.update_eval_set(
        es.id,
        EvalSetUpdate(judge_system_prompt="EXPORTED CRITERIA", version=es.version),
        "alice",
        session,
    )
    fresh = await reload(session, es.id)
    manifest = export_service.build_manifest(
        fresh, exported_by="alice", files=[], counts={}, run_ids=[], fmt="csv"
    )
    assert manifest["source"]["judge_prompt"]["system_prompt"] == "EXPORTED CRITERIA"
    assert manifest["source"]["judge_prompt"]["is_default"] is False
