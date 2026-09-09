"""Owns an uploaded script's database access, and the caps around it.

This is the backend half of the sandbox. The other half — the process, its
rlimits, its wall clock and its kill — lives in `app/sandbox/supervisor.py` and
runs in a **separate container**; see `app/sandbox/__init__.py` for why. This
file is where the credentials are, and it is the only place they ever were.

The load-bearing decision has not changed, and the split is what restores it:
**the script's process never holds the database credentials.**
`database_handler.run_sql()` in the child is a message on a pipe, forwarded
unparsed by the supervisor; this process owns the connection and answers it.
That single choice means the read-only rule, the statement timeout, the
per-query row cap and the query-count cap are all enforced somewhere the script
cannot reach, and a script that dumps every variable it can see finds no
password to dump.

What it could no longer promise, before the split, was the second half of that:
a script that read `/proc/1/environ` found the password anyway, because the
`setuid` drop this file used to perform needs the backend to run as root and the
deployment target forbids it. The sandbox container has no credentials in its
environment to be read. See `supervisor.py` for the threat table that now
belongs to it.

Enforced here, and only here:

| Abuse                                   | Defence                                |
|-----------------------------------------|----------------------------------------|
| a million queries                       | `max_queries`, counted in `_converse`  |
| one query that returns the warehouse    | `max_rows_per_query`, in `_answer`     |
| a statement that never finishes         | `statement_timeout`, server-side       |
| writing to the business database        | read-only transaction, server-side     |
| a run nobody can account for afterwards | every statement logged in `QueryLog`   |

The row cap refuses rather than truncates, deliberately: a script that computes
its answer from half the rows produces a plausible, wrong eval set, which is
worse than a failed run.
"""
from __future__ import annotations

import errno
import json
import time

from app.sandbox import protocol
from app.sandbox import transport as _transport

# Re-exported so that `script_executor.py`, `eval_set_scripts.py` and the tests
# keep one import site for the sandbox's vocabulary. They live in their own
# module because the supervisor needs `Limits` without needing a database, and
# `script_executor` needs `Limits` and `QueryError` without needing either half.
from app.sandbox.model import Limits, QueryError, QueryLog, RunResult  # noqa: F401

# The one import that crosses into the sandbox's half, and only in this
# direction: the child is stdlib-only so that it can run with no `app` package on
# its path, which means we can import it but never the reverse. Only the encoder
# is wanted here — it is what turns a Decimal or a date from the warehouse into
# something the child can be handed.
from app.services.script_runner_child import jsonable


def run_script(source: str, executor, limits: Limits | None = None, *, connect=None) -> RunResult:
    """Execute `source` in the sandbox, answering its queries through `executor`.

    `executor` needs one method, `run_sql(sql, params) -> list[dict]`, and may
    raise `QueryError`. Keeping it a parameter is what lets the containment tests
    run without a database — and it is what let the child move into a separate
    container without this signature changing.

    `connect` picks the transport. It defaults from settings rather than from an
    argument because the tests that need the in-process supervisor drive this
    through production call paths with nowhere to pass one; see
    `app/sandbox/transport.py`.
    """
    limits = limits or Limits()
    result = RunResult()
    started = time.monotonic()
    connect = connect or _transport.default_transport()
    try:
        with connect(limits) as (to_sandbox, from_sandbox):
            _converse(source, executor, limits, result, to_sandbox, from_sandbox)
    except OSError as exc:
        # A sandbox that cannot be reached is not a crash to hand the user as a
        # 500 — it is a run that did not happen, reported the same way as every
        # other failed run.
        result.error = launch_reason(exc)
    result.duration_ms = int((time.monotonic() - started) * 1000)
    return result


def _converse(source, executor, limits: Limits, result: RunResult, to_sandbox, from_sandbox):
    """The RPC loop: hand over the source, then answer queries until `done`.

    Note what the terminator is. A verdict (`ok`/`err`) says what the script
    returned, but the supervisor keeps talking after it: what the script printed,
    how the process ended and whether anything survived are only knowable once
    the child has exited and its output has been drained. So the loop runs on
    past the verdict to `done`, and a verdict that never gets one is thrown away
    rather than returned — a run we cannot account for is not a run we should
    report as clean.
    """
    try:
        to_sandbox.write(protocol.start_frame(source, limits) + "\n")
        to_sandbox.flush()
    except (BrokenPipeError, OSError):
        return

    verdict = None
    queries = 0
    while True:
        try:
            line = from_sandbox.readline()
        except OSError:
            return
        if not line:
            # The supervisor went away without a verdict *and* without a `done`.
            # Anything already collected is unaccountable, so it is not kept.
            result.value = None
            result.error = result.error or (
                "The sandbox stopped answering before the script finished."
            )
            return
        try:
            message = json.loads(line)
        except ValueError:
            result.error = "the sandbox sent a malformed message"
            return

        kind = message.get("t")
        if kind == protocol.DONE:
            _finish(message, verdict, limits, result)
            return
        if kind == protocol.OK:
            verdict = ("ok", message)
            continue
        if kind == protocol.ERR:
            verdict = ("err", message)
            continue
        if kind != protocol.SQL:
            result.error = "the sandbox sent an unexpected message"
            return

        queries += 1
        reply = _answer(message, executor, limits, result, queries)
        try:
            payload = json.dumps(reply, default=jsonable)
        except (TypeError, ValueError) as exc:
            # A column type the child cannot be handed (an ORM object, a custom
            # type). Reported as a query error so the script sees it at the
            # `run_sql` call that caused it, rather than as a mystery crash.
            payload = json.dumps(
                {"t": "qerr", "message": f"the rows could not be sent to the script: {exc}"}
            )
        try:
            to_sandbox.write(payload + "\n")
            to_sandbox.flush()
        except (BrokenPipeError, OSError):
            return


def _finish(done: dict, verdict, limits: Limits, result: RunResult) -> None:
    """Turn the supervisor's facts into the sentences the user reads.

    Every string in this function used to be composed a few lines from the
    `Popen` that produced the facts behind it. Keeping the composition here, and
    shipping only facts across the socket, is what stops the wording — and the
    tests that assert on it — from being split across a container boundary.
    """
    launch_errno = done.get("launch_errno")
    if launch_errno is not None:
        # The sandbox never got as far as running anything. Rebuilt as an OSError
        # so it goes through the same `launch_reason` as a transport failure, and
        # the EAGAIN paragraph stays what it always was.
        result.error = launch_reason(
            OSError(launch_errno, done.get("launch_strerror") or "")
        )
        return

    if verdict is not None:
        kind, message = verdict
        if kind == "ok":
            result.value = message.get("value")
        else:
            result.error = message.get("message") or "the script failed"
            result.traceback = message.get("traceback") or ""

    result.stdout = done.get("stdout") or ""
    result.stderr = done.get("stderr") or ""
    result.orphans = done.get("orphans") or 0
    if done.get("truncated"):
        result.limits_hit.append(
            f"The script printed more than {limits.max_output_chars:,} "
            "characters; the output below is truncated."
        )

    # Before the verdict below, because a refused thread usually ends the run
    # through some other exit — a signal, or an import that never finished —
    # and the reason for it is only ever in what the library printed.
    threads_refused = thread_limit_note(result.stderr)
    if threads_refused:
        _note_limit(result, threads_refused)

    if done.get("timed_out"):
        result.timed_out = True
        result.value = None
        result.error = (
            f"The script exceeded the {limits.wall_clock_s} second limit and "
            "was stopped. Narrow the query, or fetch fewer rows."
        )
    elif result.error is None and result.value is None:
        result.error = _exit_reason(done.get("returncode"), result.stderr)


def _answer(message, executor, limits: Limits, result: RunResult, queries: int) -> dict:
    sql = message.get("sql") or ""
    params = message.get("params")
    param_count = len(params) if isinstance(params, (list, dict)) else 0

    if queries > limits.max_queries:
        # The setting is named because this ceiling is a deployment choice, and
        # the person meeting it is usually the person who can move it. Without
        # the name it is a wall with no door: the value appears in no README and
        # in no document, and a mistyped `SCRIPT_*` in a `.env` is discarded
        # silently (`Settings` is `extra="ignore"`), so "I raised it and it did
        # not change" had no thread to pull.
        note = (
            f"This script ran more than {limits.max_queries} queries "
            "(the SCRIPT_MAX_QUERIES limit). Fetch what you need in fewer, "
            "larger statements — or raise the limit for this deployment."
        )
        _note_limit(result, note)
        result.queries.append(QueryLog(sql, param_count, 0, 0, error=note))
        return {"t": "qerr", "message": note}

    started = time.monotonic()
    try:
        rows = executor.run_sql(sql, params)
    except QueryError as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        result.queries.append(QueryLog(sql, param_count, 0, elapsed, error=str(exc)))
        return {"t": "qerr", "message": str(exc)}
    elapsed = int((time.monotonic() - started) * 1000)

    if len(rows) > limits.max_rows_per_query:
        note = (
            f"A query returned more than {limits.max_rows_per_query:,} rows. Add a "
            "WHERE clause or a LIMIT — the rows were not truncated, because a "
            "partial result would silently produce a wrong eval set."
        )
        _note_limit(result, note)
        result.queries.append(QueryLog(sql, param_count, len(rows), elapsed, error=note))
        return {"t": "qerr", "message": note}

    result.queries.append(QueryLog(sql, param_count, len(rows), elapsed))
    return {"t": "rows", "rows": rows}


def _note_limit(result: RunResult, note: str) -> None:
    if note not in result.limits_hit:
        result.limits_hit.append(note)


# What a library prints when the kernel refuses it a thread. OpenBLAS' wording is
# first because it is the one that has actually happened here; the second is the
# generic phrasing most C libraries use for the same EAGAIN.
_THREAD_FAILURE_MARKERS = ("blas_thread_init", "pthread_create failed")


def thread_limit_note(stderr: str) -> str | None:
    """Turn a refused thread into a sentence, or None if that is not what happened.

    Reads stderr rather than the exit status on purpose: the library prints this
    itself and then takes the process down by a route that tells us nothing —
    OpenBLAS SIGINTs it, which arrives as `KeyboardInterrupt` on whichever import
    line was executing. The advice it prints ("raise your process count limit") is
    addressed to whoever runs the machine, not to the person who uploaded a
    script, so it is replaced here rather than passed along.

    `_child_environment` pins the thread counts that cause this, so on a current
    image this should never fire. It stays because the next library to be added to
    requirements-scripts.txt may bring its own thread pool and its own variable to
    turn it off, and one clear sentence is the difference between a bug report and
    an afternoon.
    """
    if not stderr or not any(marker in stderr for marker in _THREAD_FAILURE_MARKERS):
        return None
    return (
        "A library tried to start one worker thread per processor and the sandbox "
        "refused. Numeric libraries run single-threaded here; if the script sets a "
        "thread count of its own, remove it."
    )



def launch_reason(exc: OSError) -> str:
    """Why the sandbox could not be started, in words the user can act on.

    Two shapes of failure arrive here now. One is the sandbox container refusing
    to fork — the same errnos as before, forwarded from the supervisor and
    rebuilt into an `OSError`, so the EAGAIN paragraph below is word for word
    what it was. The other is new: the sandbox could not be *reached* at all.

    Both keep the "The script could not be started: " prefix. The caller shows
    this to someone who uploaded a script, and to them the distinction between
    "the sandbox would not fork" and "the sandbox was not there" is the same
    fact — the run did not happen and it was not their file's fault.
    """
    if exc.errno == errno.EAGAIN:
        return (
            "The script could not be started: the system refused to create the "
            "sandbox process. The host has no process slots left for the user "
            "scripts run as. Try again in a moment; if it persists, this needs "
            "an administrator."
        )
    if exc.errno in (errno.ENOENT, errno.ECONNREFUSED, errno.ECONNRESET, errno.EPIPE):
        # Named separately because the action is different: nothing about the
        # script or the limits will change this, and the person who can fix it is
        # whoever runs the deployment.
        return (
            "The script could not be started: the sandbox service is not "
            "answering. Scripts run in a separate container from the rest of the "
            "application; this one is not up. Try again in a moment; if it "
            "persists, this needs an administrator."
        )
    detail = exc.strerror or str(exc)
    return f"The script could not be started: {detail}."



def _exit_reason(code: int | None, stderr: str) -> str:
    if code is not None and code < 0:
        name = signal.Signals(-code).name
        if name == "SIGXFSZ":
            return (
                "The script tried to write a file. Scripts run without disk "
                "access — return the rows from main() instead of saving them."
            )
        if name == "SIGXCPU":
            return (
                "The script used too much processor time and was stopped. "
                "Narrow the query, or do less work per row."
            )
        if name == "SIGKILL":
            return "The script was stopped — it most likely ran out of memory."
        return f"The script was terminated by {name}."
    tail = stderr.strip().splitlines()[-1:] if stderr.strip() else []
    detail = f" ({tail[0]})" if tail else ""
    return f"The script ended without returning anything{detail}."
