"""The sandboxed half of a script run. Executed as a subprocess; imports nothing.

Standalone by construction: this file must keep working with `python -I` and no
`app` package on the path, because it runs as an unprivileged user in an empty
directory with a scrubbed environment. Nothing here may import from the backend —
if it could, the script it executes could too.

Protocol (newline-delimited JSON, on file descriptors passed in argv):

    parent -> child   {"source": "..."}                       once, at startup
    child  -> parent  {"t": "sql", "sql": ..., "params": ...}  zero or more
    parent -> child   {"t": "rows", "rows": [...]}
                      {"t": "qerr", "message": "..."}
    child  -> parent  {"t": "ok",  "value": ...}               exactly one, last
                      {"t": "err", "message": ..., "traceback": ...}

The RPC deliberately does **not** run on stdout. The script will `print()`, and
stdout/stderr belong entirely to it; sharing them with the protocol would let an
innocent debug line corrupt the run.

The script never receives database credentials. `run_sql` is a message to the
parent, which owns the connection and enforces read-only access, the statement
timeout and the row caps.
"""
from __future__ import annotations

import datetime as _dt
import decimal
import json
import os
import sys
import traceback
import uuid

SCRIPT_FILENAME = "<uploaded script>"


def jsonable(obj):
    """Types that legitimately come out of a database; everything else is a bug.

    Deliberately narrow. A catch-all `default=str` would quietly turn a function
    or an ORM object into its repr and let it through as a question's text, which
    surfaces much later as a nonsense eval set rather than as an error here.
    """
    if isinstance(obj, decimal.Decimal):
        return str(obj)
    if isinstance(obj, (_dt.datetime, _dt.date, _dt.time)):
        return obj.isoformat()
    if isinstance(obj, _dt.timedelta):
        return str(obj)
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, (bytes, bytearray, memoryview)):
        return bytes(obj).decode("utf-8", "replace")
    if isinstance(obj, (set, frozenset, tuple)):
        return list(obj)
    raise TypeError(
        f"{type(obj).__name__} cannot be sent back as part of the result; "
        "convert it to a string, number or list in your script"
    )


class DatabaseError(RuntimeError):
    """A query the parent refused or the database rejected.

    Catchable without importing anything: `except Exception` works, and the class
    is reachable as `database_handler.DatabaseError` for scripts that want to be
    specific.
    """


class DatabaseHandler:
    """The single object `main()` receives.

    One method by design. Anything wider (a raw connection, a cursor) would move
    the read-only guarantee inside the sandbox, where the script could undo it.
    """

    DatabaseError = DatabaseError

    def __init__(self, send, recv):
        self._send = send
        self._recv = recv

    def run_sql(self, sql, params=None):
        """Run a read-only SQL statement and return a list of dicts.

        `params` is optional and follows psycopg's placeholders: a sequence for
        `%s`, a mapping for `%(name)s`. Use it rather than building SQL by string
        concatenation — quoting dates, NULLs and embedded apostrophes correctly by
        hand is where these scripts usually go wrong.
        """
        if not isinstance(sql, str) or not sql.strip():
            raise DatabaseError("run_sql() needs a non-empty SQL string")
        if params is not None and not isinstance(params, (list, tuple, dict)):
            raise DatabaseError(
                "run_sql() params must be a sequence (for %s) or a mapping "
                f"(for %(name)s), got {type(params).__name__}"
            )
        self._send({"t": "sql", "sql": sql, "params": params})
        reply = self._recv()
        if reply.get("t") == "rows":
            return reply["rows"]
        raise DatabaseError(reply.get("message") or "the query could not be run")

    def __repr__(self):  # keeps a stray print() from looking like a leak
        return "<database_handler: run_sql(sql, params=None)>"


def _user_traceback(exc: BaseException) -> str:
    """The script's frames only.

    Everything in this file is scaffolding the script's author did not write and
    cannot act on; leaving it in buries the one line that matters and advertises
    the host's layout for free.
    """
    lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
    kept = [
        part
        for part in lines
        if not part.lstrip().startswith("File ")
        or SCRIPT_FILENAME in part
    ]
    # If filtering removed every frame, the exception line alone is still useful.
    if not any(SCRIPT_FILENAME in part for part in kept):
        kept = [part for part in kept if not part.lstrip().startswith("File ")]
    return "".join(kept).strip()


def _add_library_path(spec):
    """Put the allowed third-party packages within reach of the uploaded script.

    `spec` is os.pathsep-separated and arrives on argv rather than in the
    environment, because the interpreter runs under `-I` and isolated mode
    ignores PYTHONPATH outright — see SCRIPT_LIBS in script_runner.py.

    Appended, never prepended: a directory of ordinary PyPI packages must not be
    able to shadow the standard library, whatever ends up installed in it.

    Missing directories are skipped rather than reported. Outside the container
    there is nothing at this path, and a developer running the tests on their own
    machine should get today's behaviour — stdlib only — not a failure about a
    directory they were never told to create.
    """
    for entry in (spec or "").split(os.pathsep):
        if entry and os.path.isdir(entry):
            sys.path.append(entry)


def main() -> int:
    req_fd, resp_fd = int(sys.argv[1]), int(sys.argv[2])
    # Optional, so that the two-argument call this file was born with still
    # works: nothing about the protocol depends on there being libraries.
    _add_library_path(sys.argv[3] if len(sys.argv) > 3 else "")
    rx = os.fdopen(req_fd, "r", encoding="utf-8")
    tx = os.fdopen(resp_fd, "w", encoding="utf-8")

    def send(msg):
        tx.write(json.dumps(msg, default=jsonable) + "\n")
        tx.flush()

    def recv():
        line = rx.readline()
        if not line:
            raise SystemExit(0)  # parent went away; nothing useful left to do
        return json.loads(line)

    boot = recv()
    source = boot["source"]

    namespace = {"__name__": "__main__", "__file__": SCRIPT_FILENAME}
    try:
        code = compile(source, SCRIPT_FILENAME, "exec")
    except SyntaxError as exc:
        send({
            "t": "err",
            "message": f"line {exc.lineno}: {exc.msg}",
            "traceback": "".join(traceback.format_exception_only(type(exc), exc)).strip(),
        })
        return 0

    try:
        exec(code, namespace)  # noqa: S102 - executing the upload is the feature
        fn = namespace.get("main")
        if fn is None or not callable(fn):
            send({
                "t": "err",
                "message": "the script defines no top-level main() function",
                "traceback": "",
            })
            return 0

        result = fn(DatabaseHandler(send, recv))
        if hasattr(result, "__await__"):
            import asyncio

            result = asyncio.run(_await(result))
    except BaseException as exc:  # noqa: BLE001 - every failure belongs to the user
        send({
            "t": "err",
            "message": _message_for(exc),
            "traceback": _user_traceback(exc),
        })
        return 0

    try:
        send({"t": "ok", "value": result})
    except (TypeError, ValueError) as exc:
        send({
            "t": "err",
            "message": f"the value main() returned could not be read back: {exc}",
            "traceback": "",
        })
    return 0


async def _await(coro):
    return await coro


def _message_for(exc: BaseException) -> str:
    if isinstance(exc, SystemExit):
        return f"the script called sys.exit({exc.code!r}) instead of returning rows"
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


if __name__ == "__main__":
    sys.exit(main())
