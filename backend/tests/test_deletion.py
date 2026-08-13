"""Deletion order.

`question_results.question_pk -> questions.id` has no ON DELETE CASCADE (§9.4,
deliberate: a locked set never deletes questions), so deleting an eval set by
cascade alone would rely on Postgres happening to remove `question_results`
before `questions` — an ordering it does not promise. These tests pin the
explicit deepest-first order, because the failure mode is a foreign-key error on
a real database that no DB-free test would otherwise catch.
"""
from __future__ import annotations

import uuid

from sqlalchemy import delete as sa_delete

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
from app.services.deletion import delete_eval_set, delete_run


class RecordingSession:
    """Records which table each DELETE targets, in order."""

    def __init__(self, ids: dict) -> None:
        self._ids = ids
        self.deleted: list[str] = []

    async def scalars(self, statement):
        # The service only selects id columns; key off the entity it came from.
        entity = statement.column_descriptions[0]["entity"]
        return _Scalars(self._ids.get(entity.__tablename__, []))

    async def execute(self, statement):
        assert isinstance(statement, type(sa_delete(Run))), "only DELETEs expected"
        self.deleted.append(statement.table.name)
        return None


class _Scalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return list(self._items)


def _ids(n: int) -> list[uuid.UUID]:
    return [uuid.uuid4() for _ in range(n)]


async def test_delete_run_removes_children_before_the_run():
    session = RecordingSession({QuestionResult.__tablename__: _ids(2)})
    await delete_run(session, uuid.uuid4())
    assert session.deleted == ["span_analyses", "question_results", "runs"]


async def test_delete_run_with_no_results_still_drops_the_run():
    session = RecordingSession({QuestionResult.__tablename__: []})
    await delete_run(session, uuid.uuid4())
    # No span_analyses statement when there is nothing to hang off.
    assert session.deleted == ["question_results", "runs"]


async def test_delete_eval_set_order_is_deepest_first():
    session = RecordingSession(
        {
            Run.__tablename__: _ids(2),
            QuestionResult.__tablename__: _ids(4),
            Question.__tablename__: _ids(3),
        }
    )
    await delete_eval_set(session, uuid.uuid4())

    assert session.deleted == [
        "span_analyses",
        "question_results",
        "runs",
        "question_skills",
        "questions",
        "eval_set_roles",
        "eval_set_scripts",
        "eval_sets",
    ]
    # The rule the FK actually enforces: question_results must be gone before
    # questions, and that is not something cascade ordering guarantees.
    assert session.deleted.index("question_results") < session.deleted.index("questions")


async def test_delete_eval_set_with_no_runs_skips_run_children():
    session = RecordingSession(
        {Run.__tablename__: [], Question.__tablename__: _ids(1)}
    )
    await delete_eval_set(session, uuid.uuid4())
    assert session.deleted == [
        "question_skills",
        "questions",
        "eval_set_roles",
        "eval_set_scripts",
        "eval_sets",
    ]


async def test_the_stored_script_goes_with_the_set_that_produced_it():
    """A set built from a Python script keeps that script for provenance; deleting
    the set must take it with it, or the source of a deleted set outlives it."""
    session = RecordingSession({Question.__tablename__: _ids(1)})
    await delete_eval_set(session, uuid.uuid4())
    assert "eval_set_scripts" in session.deleted
    assert session.deleted.index("eval_set_scripts") < session.deleted.index("eval_sets")


def test_every_child_table_is_covered():
    """A new child table added to the schema without a line in deletion.py would
    otherwise only be found by a foreign-key error in production."""
    covered = {
        SpanAnalysis.__tablename__,
        QuestionResult.__tablename__,
        Run.__tablename__,
        QuestionSkill.__tablename__,
        Question.__tablename__,
        EvalSetRole.__tablename__,
        EvalSetScript.__tablename__,
        EvalSet.__tablename__,
        # Optimize's tables are not children of an eval set and deliberately have
        # no line in deletion.py: they reference `questions` and `eval_sets` with
        # ON DELETE SET NULL, and every row that needs a question carries its own
        # snapshot of the text. An optimization run outlives the sets it drew
        # from — it belongs to no single set, and deleting one next month must
        # leave last month's run readable rather than delete it.
        # `test_optimizer_isolation.py` proves the delete path over real tables.
        "optimization_runs",
        "optimization_items",
        "optimization_steps",
        "optimization_rollouts",
        "optimization_results",
        "optimization_minibatches",
        "optimization_skills",
    }
    from app.db import Base

    assert set(Base.metadata.tables) == covered
