"""Runs a user-uploaded Python script under containment, and owns its database.

This is the security boundary for the feature. `script_validate.py` is not — it
only parses the file so the UI can tell someone they forgot `main()`. Everything
that stops a script from harming the system is here.

The load-bearing decision: **the script's process never holds the database
credentials.** `database_handler.run_sql()` in the child is a message on a pipe;
this process owns the connection and answers it. That single choice means the
read-only rule, the statement timeout, the per-query row cap and the query-count
cap are all enforced somewhere the script cannot reach, and a script that dumps
every variable it can see finds no password to dump.

What the child gets and what it is denied:

| Attack                                   | Defence                               |
|------------------------------------------|---------------------------------------|
| read `os.environ` for our secrets        | environment scrubbed to PATH/LANG/HOME |
| read /proc/1/environ, other processes    | dropped to an unprivileged uid        |
| read /app sources, .env, CA bundles      | same uid, plus an empty cwd (the      |
|                                          | child needs nothing from /app: it     |
|                                          | executes a copy — see _stage)         |
| write files, fill the disk               | RLIMIT_FSIZE = 0                      |
| fork bomb                                | RLIMIT_NPROC, own session, killpg     |
|                                          | (see the note on _preexec: the limit  |
|                                          | is per-uid and host-wide, so it is    |
|                                          | applied *after* the uid drop)         |
| memory bomb                              | RLIMIT_AS                             |
| infinite loop                            | RLIMIT_CPU + a wall clock in *this*   |
|                                          | process (a sleeping script burns no   |
|                                          | CPU, so RLIMIT_CPU alone is not enough)|
| flood stdout to exhaust our memory       | drained by threads, capped, truncated |
| abuse the RPC with a million queries     | query-count and row caps below        |
| shell injection through the interpreter  | argv list, never a shell              |

A script may also import a short list of third-party packages (pandas, tabulate);
see `SCRIPT_LIBS` below for where they come from and why that list changes none of
the above.

**Known gap, stated rather than hidden:** a subprocess in this container cannot be
denied network egress. A determined script can still reach internal services. The
`run_script` signature is the seam for closing that later — moving execution into
a network-namespaced sidecar changes this module and nothing above it. Until
then, the audit log of every run and every statement is the compensating control.
"""
from __future__ import annotations

import errno
import json
import os
import resource
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field

# The one import that crosses into the sandbox's half, and only in this
# direction: the child is stdlib-only so that it can run with no `app` package on
# its path, which means the parent can import it but never the reverse.
from app.services.script_runner_child import jsonable

# Read by *this* process and copied into each run's own directory; the child
# executes the copy and never opens this path. That is what keeps the feature
# independent of how /app happens to be mounted or chmodded — see _stage.
CHILD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "script_runner_child.py")

# Third-party packages an uploaded script is allowed to import — pandas and
# tabulate today, listed in backend/requirements-scripts.txt and installed here
# by the Dockerfile. Overridable so the tests can point at a provisioned
# directory (or at a missing one, which must stay harmless) without a container.
#
# Passed to the child on argv and appended to its sys.path there. **Not** through
# PYTHONPATH, which would look like the obvious way to do it and would silently
# do nothing: the child runs under `-I`, and isolated mode ignores PYTHONPATH,
# the user site directory and the script's own directory. Dropping `-I` to make
# the environment variable work would trade a layer of hardening for a longer
# argv, so the path travels as an argument instead.
#
# This does not widen the sandbox, and is not the place that keeps a script
# honest: containment is about what a script can *reach* — no credentials, no
# writable disk, an unprivileged uid — never about which modules it can name. A
# script has always been able to import anything the server's own site-packages
# holds (httpx, psycopg, openai are all in there). Narrowing *that* is a separate
# and larger job; this constant only adds a second directory to it, deliberately
# and in one visible place.
SCRIPT_LIBS = os.environ.get("SCRIPT_LIBS_DIR", "/opt/scriptlibs")

# Users the child is dropped to, in order of preference. The first is created by
# the backend image; `nobody` is the fallback everywhere else. Running the child
# as the same user as the server is a last resort — it is what makes /proc and
# the source tree readable to it — so it is logged loudly when it happens.
RUNNER_USERS = ("scriptrunner", "nobody")


class QueryError(RuntimeError):
    """The database refused a statement. Raised into the script, catchable there."""


@dataclass
class Limits:
    # Per query. Breaching it raises into the script rather than truncating:
    # a script that computes its answer from half the rows produces a plausible,
    # wrong eval set, which is worse than a failed run.
    max_rows_per_query: int = 50_000
    statement_timeout_s: int = 30
    wall_clock_s: int = 60
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
    orphans: int = 0


def _runner_uid() -> tuple[int, int] | None:
    """(uid, gid) to drop to, or None when we cannot drop.

    Dropping matters more than it looks: RLIMIT_NPROC is not enforced against a
    process with CAP_SYS_RESOURCE, so a child left running as root would sail
    through the fork-bomb defence.
    """
    if os.geteuid() != 0:
        return None  # unprivileged already (developer's machine); nothing to drop
    import pwd

    for name in RUNNER_USERS:
        try:
            entry = pwd.getpwnam(name)
        except KeyError:
            continue
        return entry.pw_uid, entry.pw_gid
    return None


def _child_environment(home: str) -> dict[str, str]:
    """Everything the script is allowed to see. Allow-list, never a deny-list.

    The four thread limits are not tuning; without them `import pandas` fails on
    any host with enough cores. numpy's OpenBLAS starts **one thread per core** as
    it loads, and `RLIMIT_NPROC` counts threads — per uid, across the whole host,
    for the reasons `_preexec` sets out at length. On an eight-core machine that
    is eight tasks a script has not asked for and cannot see, charged against a
    limit it shares with every other run. When `pthread_create` is refused,
    OpenBLAS does not raise: it prints its advice to stderr and SIGINTs the
    process, which reaches the author as `KeyboardInterrupt` on the import line —
    an error with no visible cause and nothing to act on.

    It has to be the environment, and it has to be here. OpenBLAS reads these
    once, while the extension module loads, so setting them from inside the script
    is already too late; and this is the last point that runs before exec, in the
    one mapping the script cannot reach. Only the first is read today — the others
    cover OpenMP, MKL and pandas' numexpr, so that a wheel built against a
    different backend does not quietly bring the bug back.

    One thread is also simply the right answer here. The work is shaping at most a
    few tens of thousands of rows, where BLAS parallelism buys nothing, and the
    backend runs a single uvicorn worker — letting each uploaded script help itself
    to every core is a way to make one upload everybody's problem.
    """
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        "HOME": home,
        "TMPDIR": home,
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
    }


def _preexec(limits: Limits):
    """Applied in the forked child, after the uid drop and before exec.

    **The uid drop is not done here, and that ordering is load-bearing.** It is
    handed to Popen's `user=`/`group=`/`extra_groups=`, which CPython performs in
    the child *before* it calls `preexec_fn` — so by the time this runs the
    process is already unprivileged, and every call below only ever lowers a
    limit, which needs no privilege.

    Doing it the other way around — the obvious order, limits then setuid — is a
    trap. `RLIMIT_NPROC` is not a per-process or per-container limit: the kernel
    counts tasks (threads included) per uid across the whole user namespace, and
    a container without userns-remap shares that namespace with the host. On
    `setuid` the kernel compares that host-wide count against the limit in force
    and, if it is over, flags the process so that the *next* `execve` fails with
    EAGAIN — surfacing as `BlockingIOError: [Errno 11] ... '/usr/local/bin/python'`
    before the script has run a single line. Whether the uid we drop to happens
    to be busy elsewhere on the host is not something this container can know, so
    the limit is applied only once the drop is already done.

    What that costs: on a host where the runner uid is already over the limit,
    the child cannot fork at all. The fork-bomb defence fails closed — the
    direction to fail in — instead of taking the whole feature down with it.

    (`preexec_fn` is documented as unsafe in the presence of threads; the output
    drain threads are started only after Popen returns, so nothing else is running
    in this process at fork time.)
    """

    def apply():
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        # No file may be written at all. The script source arrives over a pipe, so
        # nothing legitimate needs to touch the disk.
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
        resource.setrlimit(resource.RLIMIT_NPROC, (limits.max_processes, limits.max_processes))
        resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
        mem = limits.memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
        # Deliberately *past* the wall clock rather than level with it. Both would
        # race on a CPU-bound loop, and whichever won decided what the user was
        # told: "terminated by SIGXCPU" instead of "exceeded the 60 second limit".
        # The wall clock owns the message; this stays as the backstop for the case
        # it cannot cover — a child that survives the parent.
        cpu = max(2, limits.wall_clock_s + 2)
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))

    return apply


def _stage(sandbox: str) -> tuple[str, str]:
    """Lay out one run's private directory: an empty cwd, and our own module.

    The child is dropped to another uid, so it has to be able to *read* the module
    it is about to execute — and the directory that module normally lives in,
    `/app`, is the one whose permissions this process does not control: a bind
    mount of the host's checkout in development, the build context's file modes in
    the image. A host with a strict umask therefore failed every run with
    `can't open file ... [Errno 13] Permission denied`, after a perfectly good
    exec. Copying the module into the run's own directory removes the dependency
    outright: nothing under `/app` needs to be readable by the runner uid, which is
    also what makes locking `/app` down a thing this feature can survive.

    The copy is deliberately *not* put in the cwd — the script's working directory
    stays empty, as the comment there promises.

    Modes are set rather than inherited: tempfile creates 0700, and the copy's mode
    would otherwise follow this process's umask, so `umask 077` would produce a
    0600 file the child cannot read — the same bug again, in a new place.
    """
    workdir = os.path.join(sandbox, "cwd")
    bindir = os.path.join(sandbox, "bin")
    for path in (workdir, bindir):
        os.mkdir(path)
        os.chmod(path, 0o755)
    child = os.path.join(bindir, os.path.basename(CHILD))
    shutil.copyfile(CHILD, child)
    os.chmod(child, 0o644)
    return workdir, child


def _drain(stream, cap: int, into: dict, key: str) -> threading.Thread:
    """Read a child stream to exhaustion on its own thread.

    On its own thread because the RPC loop is blocking: if the script printed more
    than a pipe buffer while we sat waiting for its next message, both sides would
    stop, forever. Capped because "read it all" is a memory bomb with a friendly
    face.
    """

    def pump():
        chunks: list[str] = []
        size = 0
        truncated = False
        while True:
            block = stream.read(8192)
            if not block:
                break
            if size < cap:
                room = cap - size
                chunks.append(block[:room])
                size += min(len(block), room)
                if len(block) > room:
                    truncated = True
            else:
                truncated = True
        text = "".join(chunks)
        if truncated:
            text += f"\n… output truncated at {cap:,} characters …"
        into[key] = text
        into[key + "_truncated"] = truncated

    thread = threading.Thread(target=pump, daemon=True)
    thread.start()
    return thread


def run_script(source: str, executor, limits: Limits | None = None) -> RunResult:
    """Execute `source` in a sandbox, answering its queries through `executor`.

    `executor` needs one method, `run_sql(sql, params) -> list[dict]`, and may
    raise `QueryError`. Keeping it a parameter is what lets the containment tests
    run without a database, and what will let the whole child move into a separate
    container later without touching the caller.
    """
    limits = limits or Limits()
    result = RunResult()
    started = time.monotonic()

    # An empty, private working directory. The child cannot write to it
    # (RLIMIT_FSIZE), but a process still needs a cwd it is allowed to be in, and
    # this keeps it out of the source tree. Alongside it, out of the script's
    # sight, goes the copy of our own module the child executes — see _stage.
    import tempfile

    with tempfile.TemporaryDirectory(prefix="evalscript-") as sandbox:
        os.chmod(sandbox, 0o755)
        try:
            workdir, child = _stage(sandbox)
        except OSError as exc:
            result.error = launch_reason(exc)
            result.duration_ms = int((time.monotonic() - started) * 1000)
            return result
        credentials = _runner_uid()
        # Dropped by CPython itself rather than by our preexec_fn, because it has
        # to happen before the limits are applied — see _preexec.
        drop = {}
        if credentials:
            uid, gid = credentials
            drop = {"user": uid, "group": gid, "extra_groups": []}
        p2c_r, p2c_w = os.pipe()
        c2p_r, c2p_w = os.pipe()
        try:
            proc = subprocess.Popen(
                # -u because a killed process loses whatever is still sitting in a
                # block-buffered pipe, and the run that had to be killed is
                # precisely the one whose print() output the user needs to read.
                [sys.executable, "-I", "-B", "-u", child, str(p2c_r), str(c2p_w), SCRIPT_LIBS],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                pass_fds=(p2c_r, c2p_w),
                cwd=workdir,
                env=_child_environment(workdir),
                preexec_fn=_preexec(limits),  # noqa: PLW1509
                start_new_session=True,  # its own process group, so kills are total
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                **drop,
            )
        except BaseException as exc:
            # Nothing adopted any of the four descriptors, so all four are ours to
            # close — including the two the RPC loop below would have taken over.
            # Closing only the child's half here (the obvious `finally`) leaks two
            # per failed launch, and a feature that fails every time turns that
            # into EMFILE.
            _close_fds(p2c_r, c2p_w, p2c_w, c2p_r)
            if isinstance(exc, OSError):
                # The sandbox failing to start is not a crash to hand the user as
                # a 500 — it is a run that did not happen, and it is reported the
                # same way as every other failed run.
                result.error = launch_reason(exc)
                result.duration_ms = int((time.monotonic() - started) * 1000)
                return result
            raise
        _close_fds(p2c_r, c2p_w)

        streams: dict[str, str] = {}
        threads = [
            _drain(proc.stdout, limits.max_output_chars, streams, "stdout"),
            _drain(proc.stderr, limits.max_output_chars, streams, "stderr"),
        ]

        killed = threading.Event()

        def kill_group():
            killed.set()
            _kill(proc)

        watchdog = threading.Timer(limits.wall_clock_s, kill_group)
        watchdog.daemon = True
        watchdog.start()

        to_child = os.fdopen(p2c_w, "w", encoding="utf-8")
        from_child = os.fdopen(c2p_r, "r", encoding="utf-8")
        try:
            _converse(source, executor, limits, result, to_child, from_child)
        finally:
            watchdog.cancel()
            for handle in (to_child, from_child):
                try:
                    handle.close()
                except OSError:
                    pass
            if proc.poll() is None:
                _kill(proc)
            proc.wait()
            for thread in threads:
                thread.join(timeout=5)
            result.orphans = _survivors(proc.pid)

        result.stdout = streams.get("stdout", "")
        result.stderr = streams.get("stderr", "")
        if streams.get("stdout_truncated") or streams.get("stderr_truncated"):
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

        if killed.is_set():
            result.timed_out = True
            result.value = None
            result.error = (
                f"The script exceeded the {limits.wall_clock_s} second limit and "
                "was stopped. Narrow the query, or fetch fewer rows."
            )
        elif result.error is None and result.value is None:
            code = proc.returncode
            result.error = _exit_reason(code, result.stderr)

    result.duration_ms = int((time.monotonic() - started) * 1000)
    return result


def _converse(source, executor, limits: Limits, result: RunResult, to_child, from_child):
    """The RPC loop: hand over the source, then answer queries until a verdict."""
    try:
        to_child.write(json.dumps({"source": source}) + "\n")
        to_child.flush()
    except (BrokenPipeError, OSError):
        return

    queries = 0
    while True:
        try:
            line = from_child.readline()
        except OSError:
            return
        if not line:
            return  # the child died or finished without a verdict; caller reports it
        try:
            message = json.loads(line)
        except ValueError:
            result.error = "the sandbox sent a malformed message"
            return

        kind = message.get("t")
        if kind == "ok":
            result.value = message.get("value")
            return
        if kind == "err":
            result.error = message.get("message") or "the script failed"
            result.traceback = message.get("traceback") or ""
            return
        if kind != "sql":
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
            to_child.write(payload + "\n")
            to_child.flush()
        except (BrokenPipeError, OSError):
            return


def _answer(message, executor, limits: Limits, result: RunResult, queries: int) -> dict:
    sql = message.get("sql") or ""
    params = message.get("params")
    param_count = len(params) if isinstance(params, (list, dict)) else 0

    if queries > limits.max_queries:
        note = (
            f"This script ran more than {limits.max_queries} queries. Fetch what "
            "you need in fewer, larger statements."
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


def _close_fds(*fds: int) -> None:
    for fd in fds:
        try:
            os.close(fd)
        except OSError:
            pass


def launch_reason(exc: OSError) -> str:
    """Why the sandbox could not be started, in words the user can act on."""
    if exc.errno == errno.EAGAIN:
        return (
            "The script could not be started: the system refused to create the "
            "sandbox process. The host has no process slots left for the user "
            "scripts run as. Try again in a moment; if it persists, this needs "
            "an administrator."
        )
    detail = exc.strerror or str(exc)
    return f"The script could not be started: {detail}."


def _kill(proc) -> None:
    """SIGKILL the whole group. SIGTERM is not offered: a script that ignores it
    would keep the request hanging, and there is nothing for it to clean up."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass


def _survivors(pid: int, timeout_s: float = 2.0) -> int:
    """Whether anything is still alive in the child's process group.

    Polled rather than sampled once: a grandchild that has been SIGKILLed is a
    zombie until its parent is reaped, and a zombie still answers signal 0. A
    single check right after `wait()` therefore reports a leak that is about to
    clean itself up.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            os.killpg(pid, 0)
        except OSError:  # ProcessLookupError, PermissionError, ESRCH
            return 0
        if time.monotonic() >= deadline:
            return 1
        time.sleep(0.05)


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
