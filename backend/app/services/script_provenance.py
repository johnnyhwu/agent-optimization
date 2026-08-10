"""Turning a completed script run into the row that records it.

Small on purpose, and separate from the router, so that "what gets stored about a
script run" is one readable function rather than a dict literal buried in an
endpoint. The security property this file exists to make checkable — that no
password is ever written down — is a property of these six lines.
"""
from __future__ import annotations

import hashlib

from app.services.script_executor import DbTarget


def source_fingerprint(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def build_script_record(
    source: str, target: DbTarget, *, subject: str, row_count: int
) -> dict:
    """Columns for one `eval_set_scripts` row.

    Built from `target.audit_dict()` rather than from the target's fields
    directly: that method is the single definition of which parts of a connection
    may be written down, and it does not include the password.
    """
    audit = target.audit_dict()
    return {
        "source": source,
        "source_sha256": source_fingerprint(source),
        "db_host": audit["host"],
        "db_port": audit["port"],
        "db_name": audit["database"],
        "db_user": audit["user"],
        "row_count": row_count,
        "executed_by": subject,
    }
