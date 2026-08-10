"""The half of the sandbox that talks to the user's database.

Everything here is about the guarantees the *parent* makes on behalf of a script
that cannot make them for itself: the connection is read-only, statements cannot
run forever, and the credentials never travel any further than this process.

Needs a real PostgreSQL — read-only transactions and `statement_timeout` are the
things under test, and a fake would be testing the fake.
"""
from __future__ import annotations

import os
import re

import pytest

from app.services.script_executor import DbTarget, postgres_executor
from app.services.script_runner import Limits, QueryError, run_script

TEST_DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="set TEST_DATABASE_URL to run the database-backed script tests"
)


def target_from_url(url: str) -> DbTarget:
    m = re.match(
        r"^postgresql(?:\+\w+)?://(?P<user>[^:@]+)(?::(?P<password>[^@]*))?@"
        r"(?P<host>[^:/]+)(?::(?P<port>\d+))?/(?P<database>.+)$",
        url,
    )
    if not m:  # pragma: no cover - a misconfigured TEST_DATABASE_URL
        pytest.skip(f"cannot parse TEST_DATABASE_URL: {url}")
    g = m.groupdict()
    return DbTarget(
        host=g["host"],
        port=int(g["port"] or 5432),
        database=g["database"],
        user=g["user"],
        password=g["password"] or "",
    )


@pytest.fixture
def target():
    return target_from_url(TEST_DB)


@pytest.fixture
def limits():
    return Limits(wall_clock_s=20, statement_timeout_s=3)


def test_a_select_returns_dicts_keyed_by_column(target, limits):
    with postgres_executor(target, limits) as ex:
        rows = ex.run_sql("SELECT 1 AS n, 'billing' AS skill")
    assert rows == [{"n": 1, "skill": "billing"}]


def test_positional_params_are_bound_not_interpolated(target, limits):
    with postgres_executor(target, limits) as ex:
        rows = ex.run_sql("SELECT %s::text AS v", ["it's quoted"])
    # The apostrophe is the point: string-concatenation would have broken here,
    # which is the whole reason run_sql takes params.
    assert rows == [{"v": "it's quoted"}]


def test_named_params_are_supported(target, limits):
    with postgres_executor(target, limits) as ex:
        rows = ex.run_sql("SELECT %(team)s::text AS team", {"team": "billing"})
    assert rows == [{"team": "billing"}]


def test_writes_are_refused_by_the_transaction_not_by_inspecting_the_sql(target, limits):
    # Enforced by Postgres itself. A regex over the statement would be guessing,
    # and a script only has to write `WITH x AS (INSERT ...)` to get past one.
    with postgres_executor(target, limits) as ex:
        for statement in (
            "CREATE TABLE evil (id int)",
            "INSERT INTO pg_class VALUES (0)",
            "WITH w AS (SELECT 1) SELECT * INTO evil2 FROM w",
        ):
            with pytest.raises(QueryError) as exc:
                ex.run_sql(statement)
            assert "read-only" in str(exc.value).lower()


def test_a_slow_statement_hits_the_timeout_and_reports_it_as_such(target, limits):
    with postgres_executor(target, limits) as ex:
        with pytest.raises(QueryError) as exc:
            ex.run_sql("SELECT pg_sleep(30)")
    message = str(exc.value).lower()
    assert "timeout" in message or "timed out" in message
    assert str(limits.statement_timeout_s) in str(exc.value)


def test_a_broken_statement_reports_the_database_message(target, limits):
    with postgres_executor(target, limits) as ex:
        with pytest.raises(QueryError) as exc:
            ex.run_sql("SELECT * FROM table_that_is_not_there")
    assert "table_that_is_not_there" in str(exc.value)


def test_a_statement_returning_nothing_is_not_an_error(target, limits):
    with postgres_executor(target, limits) as ex:
        assert ex.run_sql("SELECT 1 WHERE false") == []


def test_a_failed_connection_never_echoes_the_password(target, limits):
    # Aimed at the connection *error* path rather than authentication itself: a
    # test database configured with trust auth would accept any password and make
    # an auth-failure test vacuous, while every deployment can produce this one.
    bad = DbTarget(
        host=target.host, port=target.port, database="no_such_database",
        user=target.user, password="hunter2-should-never-appear",
    )
    with pytest.raises(QueryError) as exc:
        with postgres_executor(bad, limits) as ex:
            ex.run_sql("SELECT 1")
    assert "hunter2-should-never-appear" not in str(exc.value)
    assert "no_such_database" in str(exc.value)  # still says what went wrong


def test_end_to_end_a_script_reads_the_database_through_the_sandbox(target, limits):
    source = """
def main(database_handler):
    rows = database_handler.run_sql(
        "SELECT %s::text AS q, %s::text AS a", ("How much?", "42")
    )
    return [{
        "question": rows[0]["q"],
        "ground_truth_response": rows[0]["a"],
        "ground_truth_reasoning_process_description": "Query the ledger.",
        "skill": ["billing"],
    }]
"""
    with postgres_executor(target, limits) as ex:
        result = run_script(source, ex, limits)
    assert result.error is None
    assert result.value[0]["question"] == "How much?"
    assert [q.sql for q in result.queries] == ["SELECT %s::text AS q, %s::text AS a"]


def test_end_to_end_an_insert_from_a_script_is_refused(target, limits):
    source = """
def main(database_handler):
    try:
        database_handler.run_sql("CREATE TABLE sneaky (id int)")
    except Exception as exc:
        return [{"blocked": str(exc)}]
    return [{"blocked": "no"}]
"""
    with postgres_executor(target, limits) as ex:
        result = run_script(source, ex, limits)
    assert result.error is None
    assert "read-only" in result.value[0]["blocked"].lower()
