"""The frames the backend and the supervisor exchange, and how they are framed.

Newline-delimited JSON over one AF_UNIX stream, one connection per run. The
framing is deliberately the same one the child already speaks, because most of
what crosses this socket *is* the child's protocol, forwarded verbatim:

    backend -> supervisor   {"t":"start","source":…,"limits":{…}}   once, first
    supervisor -> backend   {"t":"sql", …}        from the child, unparsed
    backend -> supervisor   {"t":"rows"|"qerr",…} to the child, unparsed
    supervisor -> backend   {"t":"ok"|"err", …}   from the child, unparsed
    supervisor -> backend   {"t":"done", …}       exactly one, last, then EOF

Two things are worth knowing about that shape.

**`ok` is not the end.** A verdict tells the backend what the script returned;
`done` tells it how the process ended, what it printed, and whether anything
survived — facts that only exist once the child has exited and its output has
been drained. So `done` is the terminator, and a verdict that arrives without
one is discarded rather than trusted: we cannot know the run was clean.

**`done` carries facts, not sentences.** Every user-visible string is composed on
the backend side, from these fields, by the code that has always composed them
(`_exit_reason`, `thread_limit_note`, the timeout paragraph). The supervisor
ships no prose, which keeps the wording — and the tests that assert on it — in
one file. The one exception is the truncation notice, which `_drain` appends
because truncation has to happen here: shipping a gigabyte across the socket in
order to shorten it at the far end is precisely the memory bomb the cap exists
to stop.

A launch failure inside the sidecar travels as the errno triple rather than as a
message, so the backend can rebuild the `OSError` and hand it to the same
`launch_reason` it has always used.
"""
from __future__ import annotations

import json

from app.sandbox.model import Limits

# Frame names. The first five are the child's own vocabulary, passing through.
START = "start"
SQL = "sql"
ROWS = "rows"
QERR = "qerr"
OK = "ok"
ERR = "err"
DONE = "done"


def kind(line: str) -> str | None:
    """The `t` of one framed line, or None if it is not a frame we can read.

    Tolerant on purpose: the supervisor calls this on lines it is forwarding
    without otherwise looking at them, and a line it cannot parse is the child's
    problem to be reported, not this function's to raise on.
    """
    try:
        message = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(message, dict):
        return None
    value = message.get("t")
    return value if isinstance(value, str) else None


def start_frame(source: str, limits: Limits) -> str:
    """What the backend sends first. Carries the script and the run's limits.

    The limits travel per run rather than being baked into the container because
    they always have: `Limits` is built per request from the deployment's
    settings, and the containment tests pass their own. A static container limit
    would quietly change what `memory_mb` and `max_processes` mean.
    """
    return json.dumps(
        {
            "t": START,
            "source": source,
            "limits": {
                "max_rows_per_query": limits.max_rows_per_query,
                "statement_timeout_s": limits.statement_timeout_s,
                "wall_clock_s": limits.wall_clock_s,
                "max_queries": limits.max_queries,
                "max_output_chars": limits.max_output_chars,
                "memory_mb": limits.memory_mb,
                "max_processes": limits.max_processes,
            },
        }
    )


# Fields of `start.limits` that the supervisor actually enforces, with the
# ceiling it will not let a caller exceed. The socket is reachable only from
# inside the pod, so this is not a trust boundary in the way the child's is —
# but a supervisor that applies whatever numbers arrive on a wire is one bug in
# the backend away from applying none at all.
_CEILINGS = {
    "wall_clock_s": 3600,
    "max_output_chars": 16 * 1024 * 1024,
    "memory_mb": 8192,
    "max_processes": 1024,
}


def limits_from_frame(message: dict) -> Limits:
    """Rebuild `Limits` from a `start` frame, clamped to what we will honour.

    Unknown keys are ignored rather than raising, so a backend one deploy ahead
    of its sidecar degrades to the older behaviour instead of failing every run.
    """
    raw = message.get("limits")
    if not isinstance(raw, dict):
        return Limits()
    fields = {}
    defaults = Limits()
    for name in (
        "max_rows_per_query",
        "statement_timeout_s",
        "wall_clock_s",
        "max_queries",
        "max_output_chars",
        "memory_mb",
        "max_processes",
    ):
        value = raw.get(name, getattr(defaults, name))
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            value = getattr(defaults, name)
        ceiling = _CEILINGS.get(name)
        if ceiling is not None:
            value = min(value, ceiling)
        fields[name] = value
    return Limits(**fields)


def done_frame(
    *,
    stdout: str = "",
    stderr: str = "",
    truncated: bool = False,
    timed_out: bool = False,
    orphans: int = 0,
    returncode: int | None = None,
    launch: OSError | None = None,
) -> str:
    """The last thing the supervisor says. Facts only — see the module docstring."""
    return json.dumps(
        {
            "t": DONE,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": truncated,
            "timed_out": timed_out,
            "orphans": orphans,
            "returncode": returncode,
            # A launch failure is shipped as its errno triple so the backend can
            # rebuild the exception and reuse `launch_reason` unchanged — which
            # is what keeps the EAGAIN paragraph byte for byte what it was.
            "launch_errno": None if launch is None else launch.errno,
            "launch_strerror": None if launch is None else launch.strerror,
        }
    )
