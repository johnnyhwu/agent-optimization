"""The vocabulary both halves of the sandbox share.

Separate from either half because `script_executor.py` needs `Limits` and
`QueryError` without needing the client, and the supervisor needs `Limits`
without needing the database. A module with no imports of its own is also what
keeps `app.sandbox.supervisor` importable in the sidecar, where most of the
application's dependencies are never loaded.
"""
from __future__ import annotations

from dataclasses import dataclass, field


class QueryError(RuntimeError):
    """The database refused a statement. Raised into the script, catchable there."""


class SandboxUnavailable(OSError):
    """The sandbox container could not be reached at all.

    A distinct type rather than an errno, because the errno cannot carry the
    distinction. `ENOENT` from the transport means "there is no socket, the
    sandbox is not running"; the very same `ENOENT`, forwarded from a healthy
    supervisor that failed to lay out a run, means "the sandbox is up and
    something inside it is wrong" — a missing staging volume, an interpreter
    that would not exec. Those send whoever is on call to opposite ends of the
    deployment, so they must not share a sentence.

    Subclasses OSError so that `run_script` keeps catching it with everything
    else that can go wrong on the way to a process.
    """


@dataclass
class Limits:
    # Per query. Breaching it raises into the script rather than truncating:
    # a script that computes its answer from half the rows produces a plausible,
    # wrong eval set, which is worse than a failed run.
    max_rows_per_query: int = 50_000
    # Kept in step with app/config.py, which is where a deployment changes them;
    # these are the fallback for a caller that passes no Limits at all.
    statement_timeout_s: int = 600
    wall_clock_s: int = 600
    max_queries: int = 50
    max_output_chars: int = 256 * 1024
    memory_mb: int = 1024
    max_processes: int = 64


@dataclass
class QueryLog:
    sql: str
    param_count: int  # never the values: they come from a business database
    rows: int
    duration_ms: int
    error: str | None = None


@dataclass
class RunResult:
    value: object | None = None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    traceback: str = ""
    limits_hit: list[str] = field(default_factory=list)
    queries: list[QueryLog] = field(default_factory=list)
    duration_ms: int = 0
    timed_out: bool = False
    # Processes left alive in the child's group after a kill. Always 0 in a
    # healthy run; asserted by the tests, because a leak here is invisible until
    # the container runs out of processes days later.
    #
    # Reported by the supervisor now rather than measured locally — the backend
    # has no view of the sidecar's process table.
    orphans: int = 0
