"""Uploading an eval set as a Python script instead of a file.

Some developers do not have a CSV to upload: their questions come out of a
business database, and they already have a script that produces them. Rather than
making them run it by hand, dump a file and upload the file, this runs it — in a
sandbox, against a database they nominate for that one request — and drops the
result into the same preview the file upload fills.

Four endpoints, and a deliberate split between the first two:

* `POST /eval-sets/script/validate` parses the file and reports what is wrong
  with its shape. No execution, no database, no credentials. The UI calls it the
  moment a file is chosen, which is what lets it withhold the password prompt
  until the script could actually work. Nobody should type a production password
  to be told they forgot `main()`.
* `POST /eval-sets/script/run` executes it. Credentials arrive here, are used to
  open one connection, and are gone when the request ends: they are not stored,
  not logged, and not echoed back.
* `GET /eval-sets/script/limits` reports the ceilings a run is held to, so that
  a limit is something you can look up rather than something you meet as an
  error naming a setting you cannot find.
* `GET /eval-sets/templates/{kind}` hands out a working example of each of the
  three upload formats.

Creating the eval set itself is *not* here. The rows this returns go through the
existing `POST /eval-sets` like any other upload, which is what keeps locking,
sharing, `question_id` generation and validation identical no matter where the
questions came from.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response
from starlette.concurrency import run_in_threadpool

from app.auth import current_subject
from app.config import settings
from app.schemas import (
    ScriptCheckOut,
    ScriptLimitsOut,
    ScriptRunOut,
    ScriptRunRequest,
    ScriptSource,
    ScriptValidationOut,
)
from app.services.script_executor import DbTarget, postgres_executor
from app.services.script_runner import Limits, QueryError, run_script
from app.services.script_schema import ScriptOutputError, validate_script_output
from app.services.script_validate import validate_script_source

log = logging.getLogger(__name__)

router = APIRouter(prefix="/eval-sets", tags=["eval-sets"])

# Bound at module level so it can be monkeypatched in tests without standing up a
# warehouse — and so that moving execution into a separate container later is a
# change to one name.
open_executor = postgres_executor

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
TEMPLATES = {
    "python": ("example_eval_set.py", "text/x-python"),
    "csv": ("example_eval_set.csv", "text/csv"),
    "jsonl": ("example_eval_set.jsonl", "application/x-ndjson"),
}

# A script is a file someone typed; a megabyte of it is not a script, it is a
# mistake or an attempt to make the parser work hard.
MAX_SOURCE_BYTES = 512 * 1024

# Script runs are the only thing in this backend that forks a process and holds a
# thread for up to a minute, and there is exactly one uvicorn worker. Without a
# ceiling, five people pressing Run at once would be five subprocesses and five
# occupied threads, and the sixth request would be a stalled page for everyone.
_slots = asyncio.Semaphore(settings.script_max_concurrent_runs)


def _limits() -> Limits:
    return Limits(
        max_rows_per_query=settings.script_max_rows_per_query,
        statement_timeout_s=settings.script_statement_timeout_s,
        wall_clock_s=settings.script_wall_clock_s,
        max_queries=settings.script_max_queries,
        max_output_chars=settings.script_max_output_chars,
        memory_mb=settings.script_memory_mb,
    )


def _checks(validation) -> list[ScriptCheckOut]:
    return [
        ScriptCheckOut(id=c.id, label=c.label, status=c.status, detail=c.detail)
        for c in validation.checks
    ]


def _guard_size(source: str) -> None:
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"The script is larger than {MAX_SOURCE_BYTES // 1024} KB.",
        )


@router.post("/script/validate", response_model=ScriptValidationOut)
async def validate_script(
    payload: ScriptSource,
    subject: str = Depends(current_subject),
) -> ScriptValidationOut:
    """Static checks only. Cheap enough to call on every file choice."""
    _guard_size(payload.source)
    validation = validate_script_source(payload.source)
    return ScriptValidationOut(ok=validation.ok, checks=_checks(validation))


@router.post("/script/run", response_model=ScriptRunOut)
async def run_script_endpoint(
    payload: ScriptRunRequest,
    subject: str = Depends(current_subject),
) -> ScriptRunOut:
    """Execute the script against the caller's database and return preview rows.

    Failures come back as a populated `error` with HTTP 200, not as a 4xx. A
    script that raised is not a malformed request — it is the normal, expected
    outcome the user is here to iterate on, and it arrives with stdout, a
    traceback and the checklist, all of which a FastAPI error body would drop.
    """
    _guard_size(payload.source)

    validation = validate_script_source(payload.source)
    checks = _checks(validation)
    if not validation.ok:
        # Nothing is connected to and nothing is executed. The UI does not offer
        # Run in this state; a client that asks anyway gets the same answer.
        failed = [c for c in checks if c.status == "fail"]
        return ScriptRunOut(
            ok=False,
            checks=checks,
            error=failed[0].detail or failed[0].label if failed else "the script is not valid",
        )

    target = DbTarget(
        host=payload.connection.host,
        port=payload.connection.port,
        database=payload.connection.database,
        user=payload.connection.user,
        password=payload.connection.password,
    )
    limits = _limits()

    async with _slots:
        # The runner blocks: it forks, then drives a synchronous RPC loop with
        # psycopg on the other end. On a single-worker event loop that would
        # freeze every other request in the building for up to a minute, so it
        # runs on a worker thread.
        result = await run_in_threadpool(_execute, payload.source, target, limits)

    _audit(subject, target, payload.source, result)

    if result.error is not None:
        return ScriptRunOut(
            ok=False,
            checks=checks,
            error=result.error,
            traceback=result.traceback,
            stdout=result.stdout,
            stderr=result.stderr,
            limits_hit=result.limits_hit,
            duration_ms=result.duration_ms,
            query_count=len(result.queries),
        )

    try:
        output = validate_script_output(result.value)
    except ScriptOutputError as exc:
        return ScriptRunOut(
            ok=False,
            checks=checks,
            error=str(exc),
            stdout=result.stdout,
            stderr=result.stderr,
            limits_hit=result.limits_hit,
            duration_ms=result.duration_ms,
            query_count=len(result.queries),
        )

    return ScriptRunOut(
        ok=True,
        checks=checks,
        rows=[row.model_dump(exclude_none=True) for row in output.rows],
        warnings=output.warnings,
        limits_hit=result.limits_hit + output.limits_hit,
        stdout=result.stdout,
        stderr=result.stderr,
        duration_ms=result.duration_ms,
        query_count=len(result.queries),
    )


def _execute(source: str, target: DbTarget, limits: Limits):
    """Open the connection and run, translating a connection failure into a result.

    A database that cannot be reached is the single most common way this fails,
    and it is the user's typo to fix — so it comes back as a readable error on the
    run, not as a 500.

    `OSError` is caught for the same reason: the run failing to start at all is
    still a failed run, and this endpoint's contract is that failed runs arrive as
    a 200 carrying `error`, checks and output — none of which survive a 500.
    `run_script` already translates a sandbox that will not launch, so this is the
    backstop for the rest (the connection, the executor).
    """
    from app.services.script_runner import RunResult, launch_reason

    try:
        with open_executor(target, limits) as executor:
            return run_script(source, executor, limits)
    except QueryError as exc:
        return RunResult(error=str(exc))
    except OSError as exc:
        log.exception("eval-set script run could not start")
        return RunResult(error=launch_reason(exc))


def _audit(subject: str, target: DbTarget, source: str, result) -> None:
    """The record of what ran, where, and what it asked for.

    This is the compensating control for the one thing an in-container sandbox
    cannot prevent — outbound network access — so it is written for every run,
    including the ones that failed. `DbTarget.audit_dict()` is what may be
    recorded; the password is not part of it, and `DbTarget.__repr__` masks it in
    case this object is ever formatted by something less careful.
    """
    from app.services.script_provenance import source_fingerprint

    log.info(
        "eval-set script run: subject=%s target=%s script_sha256=%s "
        "duration_ms=%s queries=%s outcome=%s",
        subject,
        target.audit_dict(),
        source_fingerprint(source)[:16],
        result.duration_ms,
        len(result.queries),
        "error" if result.error else "ok",
    )
    for entry in result.queries:
        # The statement, never the parameter values: those come straight out of a
        # business database and can be personal data.
        log.info(
            "eval-set script sql: subject=%s rows=%s duration_ms=%s params=%s sql=%s",
            subject,
            entry.rows,
            entry.duration_ms,
            entry.param_count,
            entry.sql,
        )


@router.get("/script/limits", response_model=ScriptLimitsOut)
def script_limits() -> ScriptLimitsOut:
    """What this deployment will actually let a script do.

    These numbers were previously unknowable from outside the process. They are
    not in the README, not in `docs/spec.md`, and the only place any of their
    names appeared was one line of `docker-compose.yml` — so the first time
    anyone met one was as an error, naming a ceiling they had no way to look up
    and no way to confirm they had raised. `Settings` is `extra="ignore"`, which
    means a misspelled `SCRIPT_*` in a `.env` is discarded in silence and the
    limit simply stays where it was, with nothing anywhere reporting a problem.

    Serving them turns that into a page refresh: the upload dialog prints these
    beside the editor, so a raised limit that did not take effect is visible
    before a script is ever run.
    """
    limits = _limits()
    # Named one by one rather than splatted from the dataclass: `Limits` also
    # carries containment knobs that are not a script author's business
    # (`max_processes`), and a future field should reach this response because
    # somebody decided it should, not because it happened to be added upstairs.
    return ScriptLimitsOut(
        max_rows_per_query=limits.max_rows_per_query,
        statement_timeout_s=limits.statement_timeout_s,
        wall_clock_s=limits.wall_clock_s,
        max_queries=limits.max_queries,
        max_output_chars=limits.max_output_chars,
        memory_mb=limits.memory_mb,
    )


@router.get("/templates/{kind}")
def template(kind: str) -> Response:
    """A working example of one upload format.

    Served from files on disk rather than string literals so that the Python
    example is a real module — it is linted and imported by the tests, which is
    what keeps the example we hand out in step with the rules we enforce.
    """
    if kind not in TEMPLATES:
        raise HTTPException(status_code=404, detail=f"no template named {kind!r}")
    name, media_type = TEMPLATES[kind]
    body = (EXAMPLES / name).read_bytes()
    return Response(
        content=body,
        media_type=media_type,
        headers={"content-disposition": f'attachment; filename="{name}"'},
    )
