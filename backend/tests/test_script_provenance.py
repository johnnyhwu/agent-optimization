"""What is kept about a script-built eval set, and what is deliberately not.

"How was this eval set produced?" is the question provenance exists to answer, so
the script itself is stored alongside the set. The connection it ran against is
recorded too — minus the password, which is the one field that must never reach
the database, a log line, or an API response.
"""
from __future__ import annotations

import hashlib

from app.models import EvalSetScript
from app.services.script_executor import DbTarget
from app.services.script_provenance import build_script_record

SOURCE = "def main(database_handler):\n    return []\n"
TARGET = DbTarget(
    host="warehouse.internal", port=5432, database="sales",
    user="reader", password="never-store-me",
)


def test_the_script_source_is_kept_verbatim():
    record = build_script_record(SOURCE, TARGET, subject="alice", row_count=12)
    assert record["source"] == SOURCE


def test_the_source_is_fingerprinted_so_two_sets_can_be_compared():
    record = build_script_record(SOURCE, TARGET, subject="alice", row_count=12)
    assert record["source_sha256"] == hashlib.sha256(SOURCE.encode()).hexdigest()


def test_the_connection_is_recorded_without_the_password():
    record = build_script_record(SOURCE, TARGET, subject="alice", row_count=12)
    assert record["db_host"] == "warehouse.internal"
    assert record["db_port"] == 5432
    assert record["db_name"] == "sales"
    assert record["db_user"] == "reader"
    assert "never-store-me" not in repr(record)


def test_no_column_on_the_model_could_hold_a_password():
    # A column named for a secret is how a secret ends up stored by accident three
    # refactors from now. There is no such column, and this fails if one appears.
    names = set(EvalSetScript.__table__.columns.keys())
    assert not any(
        word in name for name in names for word in ("password", "secret", "credential")
    )


def test_every_recorded_field_is_a_real_column():
    record = build_script_record(SOURCE, TARGET, subject="alice", row_count=12)
    columns = set(EvalSetScript.__table__.columns.keys())
    assert set(record) <= columns


def test_who_ran_it_and_how_much_it_produced_are_kept():
    record = build_script_record(SOURCE, TARGET, subject="alice", row_count=312)
    assert record["executed_by"] == "alice"
    assert record["row_count"] == 312
