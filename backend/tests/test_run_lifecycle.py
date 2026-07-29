"""Cancel and delete: who may do it, and when.

Endpoint functions are called directly with a stub session — the rules being
protected are the guards and the state checks, not FastAPI's wiring.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app import cancellation
from app.models import EvalSet, EvalSetRole, Run
from app.routers import eval_sets as eval_sets_router
from app.routers import runs as runs_router

EVAL_SET_ID = uuid.uuid4()


class StubSession:
    def __init__(self, run: Run | None = None, role: str | None = "owner",
                 running_count: int = 0, eval_set: EvalSet | None = None) -> None:
        self._run = run
        self._role = role
        self._running_count = running_count
        self._eval_set = eval_set if eval_set is not None else EvalSet(name="set")
        self.commits = 0
        self.deleted: list[str] = []

    async def get(self, model, pk):
        if model is Run:
            return self._run if self._run is not None and self._run.id == pk else None
        if model is EvalSet:
            return self._eval_set
        return None

    async def scalar(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        # role_for() selects EvalSetRole.role; everything else here is a count.
        return self._role if entity is EvalSetRole else self._running_count

    async def scalars(self, _statement):
        return _Scalars([])

    async def execute(self, statement):
        self.deleted.append(statement.table.name)
        return None

    async def commit(self):
        self.commits += 1


class _Scalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return list(self._items)


def make_run(status="running", triggered_by="bob") -> Run:
    run = Run(triggered_by=triggered_by, status=status, eval_set_id=EVAL_SET_ID)
    run.id = uuid.uuid4()
    run.cancel_requested = False
    run.config = {}
    run.secrets = {}
    run.pass_rate = None
    run.total_count = None
    run.correct_count = None
    run.started_at = datetime(2026, 7, 29, tzinfo=timezone.utc)
    run.completed_at = None
    run.name = None
    return run


@pytest.fixture(autouse=True)
def clean_cancellation():
    yield
    cancellation._events.clear()


# --- cancel ------------------------------------------------------------------

async def test_the_person_who_started_the_run_may_cancel_it():
    """A viewer may trigger a run (§6.16); someone who can start a run against a
    real agent must be able to stop it."""
    run = make_run(triggered_by="bob")
    session = StubSession(run, role="viewer")

    await runs_router.cancel_run(EVAL_SET_ID, run.id, subject="bob", session=session)

    assert run.cancel_requested is True
    assert cancellation.is_cancelled(run.id)


async def test_an_owner_may_cancel_someone_elses_run():
    run = make_run(triggered_by="bob")
    session = StubSession(run, role="owner")

    await runs_router.cancel_run(EVAL_SET_ID, run.id, subject="alice", session=session)

    assert run.cancel_requested is True


async def test_another_viewer_may_not_cancel_a_run_they_did_not_start():
    run = make_run(triggered_by="bob")
    session = StubSession(run, role="viewer")

    with pytest.raises(HTTPException) as exc:
        await runs_router.cancel_run(EVAL_SET_ID, run.id, subject="carol", session=session)
    assert exc.value.status_code == 403
    assert run.cancel_requested is False
    assert not cancellation.is_cancelled(run.id)


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
async def test_cancelling_a_finished_run_is_a_conflict(status):
    run = make_run(status=status, triggered_by="alice")
    session = StubSession(run, role="owner")

    with pytest.raises(HTTPException) as exc:
        await runs_router.cancel_run(EVAL_SET_ID, run.id, subject="alice", session=session)
    assert exc.value.status_code == 409


async def test_cancelling_a_run_from_another_eval_set_is_a_404():
    run = make_run()
    run.eval_set_id = uuid.uuid4()  # belongs elsewhere
    session = StubSession(run, role="owner")

    with pytest.raises(HTTPException) as exc:
        await runs_router.cancel_run(EVAL_SET_ID, run.id, subject="alice", session=session)
    assert exc.value.status_code == 404


# --- delete run --------------------------------------------------------------

async def test_deleting_a_finished_run_removes_its_rows():
    run = make_run(status="completed")
    session = StubSession(run)

    response = await runs_router.delete_run(
        EVAL_SET_ID, run.id, subject="alice", session=session
    )

    assert response.status_code == 204
    assert session.deleted == ["question_results", "runs"]
    assert session.commits == 1


async def test_a_running_run_must_be_cancelled_before_it_can_be_deleted():
    """The orchestrator is still writing to those rows — the stop button is the
    way out, not a delete that pulls the table out from under it."""
    run = make_run(status="running")
    session = StubSession(run)

    with pytest.raises(HTTPException) as exc:
        await runs_router.delete_run(EVAL_SET_ID, run.id, subject="alice", session=session)
    assert exc.value.status_code == 409
    assert session.deleted == []


# --- delete eval set ---------------------------------------------------------

async def test_deleting_an_eval_set_removes_the_whole_tree():
    session = StubSession(running_count=0)

    response = await eval_sets_router.delete_eval_set(
        EVAL_SET_ID, subject="alice", session=session
    )

    assert response.status_code == 204
    assert session.deleted[-1] == "eval_sets"
    assert "questions" in session.deleted


async def test_an_eval_set_with_a_run_in_flight_is_not_deletable():
    session = StubSession(running_count=1)

    with pytest.raises(HTTPException) as exc:
        await eval_sets_router.delete_eval_set(EVAL_SET_ID, subject="alice", session=session)
    assert exc.value.status_code == 409
    assert session.deleted == []
