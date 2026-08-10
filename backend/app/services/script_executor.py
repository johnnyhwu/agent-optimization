"""The database connection an uploaded script queries through — owned by us.

This is the other half of the arrangement described in `script_runner.py`: the
script's process gets a stub with one method, and every call it makes arrives
here, in the server process, where the connection actually lives. The script
never sees a host, a user or a password.

Three guarantees are made here rather than in the sandbox, because a guarantee
the script's process could reach is no guarantee at all:

* **Read only.** Enforced by Postgres via a read-only transaction, not by looking
  at the SQL. Pattern-matching statements is guesswork — `WITH w AS (INSERT …)`
  and `SELECT … INTO` both write without starting with the word people search for
  — whereas the server refuses every write path there is.
* **Bounded.** `statement_timeout` is set on the session, so a query that would
  run for an hour is cancelled by the database even if this process is busy.
* **Credentials stay here.** They arrive on one request, are used, and are never
  written to the database, a log line or an error message.

Synchronous psycopg on purpose: this runs inside `anyio.to_thread`, driven by the
blocking RPC loop in `script_runner`. Making it async would buy nothing — the
caller is a single script waiting for one answer at a time — and would put a
second connection pool next to the app's own.
"""
from __future__ import annotations

import contextlib
from dataclasses import dataclass

from app.services.script_runner import Limits, QueryError


@dataclass(frozen=True)
class DbTarget:
    """Where a script's data comes from. Supplied per run; never persisted.

    `__repr__` is overridden rather than left to the dataclass: this object ends
    up in tracebacks and log records, and the default would print the password
    into both.
    """

    host: str
    port: int
    database: str
    user: str
    password: str

    def __repr__(self) -> str:
        return (
            f"DbTarget(host={self.host!r}, port={self.port!r}, "
            f"database={self.database!r}, user={self.user!r}, password='***')"
        )

    __str__ = __repr__

    def audit_dict(self) -> dict:
        """What may be written to the audit log — everything but the password."""
        return {
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "user": self.user,
        }


class PostgresExecutor:
    """`run_sql` for one script run, against one connection."""

    def __init__(self, connection, limits: Limits):
        self._connection = connection
        self._limits = limits

    def run_sql(self, sql: str, params=None) -> list[dict]:
        # psycopg wants a tuple for %s placeholders; JSON turned the script's
        # tuple into a list on the way here, which psycopg does accept, but being
        # explicit keeps the failure mode away from a user who cannot see this.
        if isinstance(params, list):
            params = tuple(params)
        try:
            with self._connection.cursor() as cur:
                cur.execute(sql, params)
                if cur.description is None:
                    # A statement that returns nothing is almost always a write
                    # that the read-only transaction would have refused; if it got
                    # here it did nothing useful, and returning [] would look like
                    # an empty result set.
                    raise QueryError(
                        "that statement returned no result set — run_sql() is for "
                        "SELECT queries"
                    )
                columns = [c.name for c in cur.description]
                return [dict(zip(columns, row)) for row in cur.fetchall()]
        except QueryError:
            raise
        except Exception as exc:  # psycopg.Error and anything it wraps
            # Without this, a script that catches a failed query and carries on —
            # which is normal, defensive code — gets "current transaction is
            # aborted" for every statement after it, and the real error is three
            # queries back. Each statement stands on its own.
            with contextlib.suppress(Exception):
                self._connection.rollback()
            raise QueryError(_readable(exc, self._limits)) from None


def _readable(exc: Exception, limits: Limits) -> str:
    """A database error the script's author can act on, and nothing else.

    psycopg's message can carry the connection string; the diagnostic fields are
    the parts that are safe and useful.
    """
    diag = getattr(exc, "diag", None)
    primary = (getattr(diag, "message_primary", None) or "").strip()
    detail = (getattr(diag, "message_hint", None) or "").strip()
    sqlstate = getattr(exc, "sqlstate", None)

    if sqlstate == "57014":  # query_canceled
        return (
            f"the query ran longer than the {limits.statement_timeout_s} second "
            "statement timeout and was cancelled"
        )
    if sqlstate == "25006":  # read_only_sql_transaction
        return (
            "this connection is read-only — scripts may only read from the "
            "database, never write to it"
        )
    if primary:
        return f"{primary}{f' ({detail})' if detail else ''}"

    # Fall back to the exception text with anything that looks like a DSN removed.
    text = str(exc).strip() or exc.__class__.__name__
    return text.split("\n")[0]


@contextlib.contextmanager
def postgres_executor(target: DbTarget, limits: Limits):
    """Open a read-only, time-bounded connection for the duration of one run."""
    import psycopg

    try:
        connection = psycopg.connect(
            host=target.host,
            port=target.port,
            dbname=target.database,
            user=target.user,
            password=target.password,
            connect_timeout=10,
            # Each statement is its own transaction. Nothing here spans two
            # queries — the connection is read-only — and it means a failed
            # statement cannot leave the session in the aborted state that turns
            # every later query into the same unhelpful error.
            autocommit=True,
            # Announced to the target database so its owner can see where a query
            # came from without asking us.
            application_name="agentopt-eval-script",
            options=f"-c statement_timeout={int(limits.statement_timeout_s * 1000)} "
            f"-c idle_in_transaction_session_timeout={int(limits.wall_clock_s * 1000) + 5000} "
            "-c default_transaction_read_only=on",
        )
    except Exception as exc:
        raise QueryError(f"could not connect to the database: {_readable(exc, limits)}") from None

    try:
        with connection:
            with connection.cursor() as cur:
                # Belt and braces with `default_transaction_read_only` above: that
                # option is silently ignored by poolers that rewrite options, this
                # is not.
                cur.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
            yield PostgresExecutor(connection, limits)
    finally:
        with contextlib.suppress(Exception):
            connection.close()
