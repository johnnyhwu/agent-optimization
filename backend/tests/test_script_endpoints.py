"""The HTTP surface of script upload, and what it must never hand back.

Two things are load-bearing here and are asserted rather than assumed:

* the password the user types to run their script is used and forgotten — it
  reaches no response body, no log record and no database row;
* creating an eval set from a script produces exactly the same result as creating
  one from a file, because it goes through the same endpoint. The regression test
  for the CSV/JSONL path lives here for that reason.
"""
from __future__ import annotations

import logging
import os
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models import EvalSet, EvalSetScript, Question
from app.routers import eval_set_scripts as scripts_router
from app.routers import eval_sets as eval_sets_router
from app.schemas import (
    EvalSetCreate,
    ScriptProvenance,
    ScriptRunRequest,
    ScriptSource,
    ScriptTarget,
)

TEST_DB = os.environ.get("TEST_DATABASE_URL")
pytestmark_db = pytest.mark.skipif(
    not TEST_DB, reason="set TEST_DATABASE_URL to run the database-backed script tests"
)

PASSWORD = "correct-horse-battery-staple"

GOOD_SCRIPT = """
def main(database_handler) -> list[dict]:
    rows = database_handler.run_sql("SELECT 1 AS n")
    return [{
        "question": "How much did ACME owe?",
        "ground_truth_response": "$42,180",
        "ground_truth_reasoning_process_description": "Sum the open invoices.",
        "skill": ["billing"],
    } for _ in rows]
"""


def target(**over):
    fields = {
        "host": "127.0.0.1", "port": 5432, "database": "warehouse",
        "user": "reader", "password": PASSWORD,
    }
    fields.update(over)
    return ScriptTarget(**fields)


# --- validate: no database, no execution -------------------------------------

async def test_validate_returns_the_checklist_for_a_good_script():
    out = await scripts_router.validate_script(ScriptSource(source=GOOD_SCRIPT), subject="alice")
    assert out.ok
    assert [c.status for c in out.checks] == ["pass"] * len(out.checks)


async def test_validate_reports_a_missing_main_without_running_anything():
    out = await scripts_router.validate_script(
        ScriptSource(source="def helper(x):\n    return x\n"), subject="alice"
    )
    assert not out.ok
    failed = [c for c in out.checks if c.status == "fail"]
    assert [c.id for c in failed] == ["has_main"]
    assert "main" in failed[0].detail


async def test_validate_refuses_an_oversized_upload():
    with pytest.raises(HTTPException) as exc:
        await scripts_router.validate_script(
            ScriptSource(source="x = 1\n" * 400_000), subject="alice"
        )
    assert exc.value.status_code == 413


# --- run ---------------------------------------------------------------------

async def test_run_executes_the_script_and_returns_preview_rows(monkeypatch):
    _stub_executor(monkeypatch, rows=[{"n": 1}])
    out = await scripts_router.run_script_endpoint(
        ScriptRunRequest(source=GOOD_SCRIPT, connection=target()), subject="alice"
    )
    assert out.error is None
    assert len(out.rows) == 1
    # Wire names, so the browser reuses the JSONL row mapping it already has.
    assert out.rows[0]["ground_truth_response"] == "$42,180"
    assert out.rows[0]["skill"] == ["billing"]


async def test_run_will_not_start_a_script_that_fails_static_checks(monkeypatch):
    calls = _stub_executor(monkeypatch, rows=[{"n": 1}])
    out = await scripts_router.run_script_endpoint(
        ScriptRunRequest(source="def helper():\n    pass\n", connection=target()),
        subject="alice",
    )
    assert out.error is not None
    assert not out.ok
    # Nothing connected: a script that cannot work must not reach the database.
    assert calls == []


async def test_run_reports_row_problems_as_warnings_and_keeps_the_good_rows(monkeypatch):
    _stub_executor(monkeypatch, rows=[{"n": 1}])
    source = """
def main(database_handler):
    database_handler.run_sql("SELECT 1")
    return [
        {"question": "a", "ground_truth_response": "b",
         "ground_truth_reasoning_process_description": "c", "skill": ["billing"]},
        {"question": "no skill", "ground_truth_response": "b",
         "ground_truth_reasoning_process_description": "c", "skill": []},
    ]
"""
    out = await scripts_router.run_script_endpoint(
        ScriptRunRequest(source=source, connection=target()), subject="alice"
    )
    assert len(out.rows) == 1
    assert len(out.warnings) == 1
    assert out.warnings[0].startswith("item 2:")
    assert out.error is None  # partial success is still success


async def test_run_returns_script_output_so_prints_are_not_lost(monkeypatch):
    _stub_executor(monkeypatch, rows=[{"n": 1}])
    source = """
def main(database_handler):
    print("fetched 1 row")
    return [{"question": "a", "ground_truth_response": "b",
             "ground_truth_reasoning_process_description": "c", "skill": ["s"]}]
"""
    out = await scripts_router.run_script_endpoint(
        ScriptRunRequest(source=source, connection=target()), subject="alice"
    )
    assert "fetched 1 row" in out.stdout


async def test_run_surfaces_a_script_exception_with_its_traceback(monkeypatch):
    _stub_executor(monkeypatch, rows=[])
    source = "def main(database_handler):\n    raise ValueError('no data for Q2')\n"
    out = await scripts_router.run_script_endpoint(
        ScriptRunRequest(source=source, connection=target()), subject="alice"
    )
    assert "no data for Q2" in out.error
    assert "<uploaded script>" in out.traceback


async def test_run_reports_a_connection_failure_as_an_error_not_a_crash(monkeypatch):
    from app.services.script_runner import QueryError

    def boom(*a, **k):
        raise QueryError("could not connect to the database: no such host")

    monkeypatch.setattr(scripts_router, "open_executor", boom)
    out = await scripts_router.run_script_endpoint(
        ScriptRunRequest(source=GOOD_SCRIPT, connection=target()), subject="alice"
    )
    assert out.error is not None
    assert "no such host" in out.error


async def test_run_reports_a_sandbox_that_will_not_start_as_an_error_not_a_500(monkeypatch):
    # The failure this covers took the whole endpoint down with a 500 — the user
    # saw a broken page instead of the checklist and a sentence they could act on.
    import errno

    def boom(*a, **k):
        raise BlockingIOError(errno.EAGAIN, os.strerror(errno.EAGAIN), "/usr/local/bin/python")

    monkeypatch.setattr(scripts_router, "open_executor", boom)
    out = await scripts_router.run_script_endpoint(
        ScriptRunRequest(source=GOOD_SCRIPT, connection=target()), subject="alice"
    )
    assert out.ok is False
    assert out.error is not None
    assert "could not be started" in out.error
    # The checklist still comes back, which is the point of not raising.
    assert out.checks


# --- the password ------------------------------------------------------------

async def test_the_password_never_appears_in_the_response(monkeypatch):
    _stub_executor(monkeypatch, rows=[{"n": 1}])
    out = await scripts_router.run_script_endpoint(
        ScriptRunRequest(source=GOOD_SCRIPT, connection=target()), subject="alice"
    )
    assert PASSWORD not in out.model_dump_json()


async def test_the_password_never_appears_in_a_log_record(monkeypatch, caplog):
    _stub_executor(monkeypatch, rows=[{"n": 1}])
    with caplog.at_level(logging.DEBUG):
        await scripts_router.run_script_endpoint(
            ScriptRunRequest(source=GOOD_SCRIPT, connection=target()), subject="alice"
        )
    assert PASSWORD not in caplog.text
    # The run is audited even so: who, where, and what it ran.
    assert "alice" in caplog.text
    assert "warehouse" in caplog.text


async def test_the_password_is_not_echoed_when_the_script_fails(monkeypatch, caplog):
    _stub_executor(monkeypatch, rows=[])
    source = "def main(database_handler):\n    raise RuntimeError('boom')\n"
    with caplog.at_level(logging.DEBUG):
        out = await scripts_router.run_script_endpoint(
            ScriptRunRequest(source=source, connection=target()), subject="alice"
        )
    assert PASSWORD not in out.model_dump_json()
    assert PASSWORD not in caplog.text


def test_provenance_has_no_password_field_at_all():
    # Not "we remember not to send it" — the field does not exist, and a payload
    # carrying one is refused rather than quietly accepted and dropped.
    assert "password" not in ScriptProvenance.model_fields
    with pytest.raises(Exception):
        ScriptProvenance(
            source="x", db_host="h", db_port=1, db_name="d", db_user="u",
            password="leak",
        )


async def test_every_statement_the_script_ran_is_audited(monkeypatch, caplog):
    # The compensating control for the one thing the in-container sandbox cannot
    # prevent (network egress): if a script does something it should not, the
    # record of what it asked the database for is what makes that visible.
    _stub_executor(monkeypatch, rows=[{"n": 1}])
    with caplog.at_level(logging.INFO):
        await scripts_router.run_script_endpoint(
            ScriptRunRequest(source=GOOD_SCRIPT, connection=target()), subject="alice"
        )
    assert "SELECT 1 AS n" in caplog.text


# --- templates ---------------------------------------------------------------

@pytest.mark.parametrize("kind", ["python", "csv", "jsonl"])
def test_each_template_is_served_with_a_filename(kind):
    response = scripts_router.template(kind)
    body = response.body.decode()
    assert body.strip()
    assert "attachment" in response.headers["content-disposition"]


def test_the_python_template_passes_our_own_static_checks():
    # The example we hand out must satisfy the rules we enforce; a template that
    # fails validation is the worst possible first experience.
    from app.services.script_validate import validate_script_source

    body = scripts_router.template("python").body.decode()
    assert validate_script_source(body).ok


def test_the_jsonl_template_parses_with_the_existing_uploader():
    from app.services.upload import parse_jsonl

    body = scripts_router.template("jsonl").body.decode()
    parsed = parse_jsonl(body)
    assert parsed.errors == []
    assert len(parsed.questions) >= 2


def test_an_unknown_template_is_a_404():
    with pytest.raises(HTTPException) as exc:
        scripts_router.template("exe")
    assert exc.value.status_code == 404


# --- creation with provenance ------------------------------------------------

@pytestmark_db
async def test_a_script_built_set_stores_its_script(session_factory):
    async with session_factory() as session:
        out = await eval_sets_router.create_eval_set(
            EvalSetCreate(
                name="From script",
                jsonl='{"question": "q", "ground_truth_response": "r", '
                '"ground_truth_reasoning_process_description": "g", "skill": ["billing"]}',
                source_format="python",
                script=ScriptProvenance(
                    source=GOOD_SCRIPT, db_host="warehouse.internal", db_port=5432,
                    db_name="sales", db_user="reader",
                ),
            ),
            subject="alice",
            session=session,
        )
    async with session_factory() as session:
        stored = (
            await session.scalars(
                select(EvalSetScript).where(EvalSetScript.eval_set_id == uuid.UUID(out["id"]))
            )
        ).one()
        assert stored.source == GOOD_SCRIPT
        assert stored.db_name == "sales"
        assert stored.executed_by == "alice"
        assert stored.row_count == 1
        es = await session.get(EvalSet, uuid.UUID(out["id"]))
        assert es.source_format == "python"


@pytestmark_db
async def test_the_csv_path_is_completely_unchanged(session_factory):
    """The regression that matters most: uploading a file must behave exactly as
    it did before scripts existed — no script row, no new required field."""
    async with session_factory() as session:
        out = await eval_sets_router.create_eval_set(
            EvalSetCreate(
                name="From CSV",
                jsonl='{"question": "q", "ground_truth_response": "r", '
                '"ground_truth_reasoning_process_description": "g", "skill": ["billing"]}',
                source_format="csv",
            ),
            subject="alice",
            session=session,
        )
    async with session_factory() as session:
        es = await session.get(EvalSet, uuid.UUID(out["id"]))
        assert es.source_format == "csv"
        scripts = (
            await session.scalars(
                select(EvalSetScript).where(EvalSetScript.eval_set_id == es.id)
            )
        ).all()
        assert scripts == []
        questions = (
            await session.scalars(select(Question).where(Question.eval_set_id == es.id))
        ).all()
        assert len(questions) == 1


@pytestmark_db
async def test_provenance_is_ignored_for_a_file_upload(session_factory):
    # Belt and braces: a client that sends both must not end up with a script row
    # attached to a set that no script produced.
    async with session_factory() as session:
        out = await eval_sets_router.create_eval_set(
            EvalSetCreate(
                name="Mixed signals",
                jsonl='{"question": "q", "ground_truth_response": "r", '
                '"ground_truth_reasoning_process_description": "g", "skill": ["s"]}',
                source_format="jsonl",
                script=ScriptProvenance(
                    source=GOOD_SCRIPT, db_host="h", db_port=1, db_name="d", db_user="u"
                ),
            ),
            subject="alice",
            session=session,
        )
    async with session_factory() as session:
        scripts = (
            await session.scalars(
                select(EvalSetScript).where(
                    EvalSetScript.eval_set_id == uuid.UUID(out["id"])
                )
            )
        ).all()
        assert scripts == []


# --- helpers -----------------------------------------------------------------

def _stub_executor(monkeypatch, rows):
    """Replace the Postgres connection with something that records its calls.

    The runner and the executor have their own tests; these are about the
    endpoint, and standing up a warehouse to test an HTTP handler would make the
    suite slower and less specific.
    """
    import contextlib

    calls: list = []

    class Stub:
        def run_sql(self, sql, params):
            calls.append((sql, params))
            return list(rows)

    @contextlib.contextmanager
    def opener(target_, limits):
        calls.append(("connect", target_.host))
        yield Stub()

    monkeypatch.setattr(scripts_router, "open_executor", opener)
    calls.clear()
    return calls


@pytest.fixture
async def session_factory():
    if not TEST_DB:
        pytest.skip("no database")
    engine = create_async_engine(TEST_DB)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()
