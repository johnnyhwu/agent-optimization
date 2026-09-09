"""Runs the uploaded script as a child process, inside the sidecar container.

This is the half of the sandbox that touches processes. It is started by
`server.py` as `python -m app.sandbox.server` in a container that has **a
different uid from the backend, none of the backend's environment variables, no
CA material and no database connection** — see `app/sandbox/__init__.py` for why
that container exists at all.

What the child gets and what it is denied:

| Attack                                   | Defence                                |
|------------------------------------------|---------------------------------------|
| read `os.environ` for our secrets        | environment scrubbed to PATH/LANG/HOME |
| read /proc/1/environ, other processes    | this container holds no secrets to     |
|                                          | find; PID 1 here is the supervisor and |
|                                          | the pod does not share a PID namespace |
| read /app sources, .env, CA bundles      | not mounted here, plus an empty cwd    |
|                                          | (the child needs nothing from /app: it |
|                                          | executes a copy — see _stage)          |
| write files, fill the disk               | RLIMIT_FSIZE = 0                       |
| fork bomb                                | RLIMIT_NPROC, own session, killpg      |
| memory bomb                              | RLIMIT_AS                              |
| infinite loop                            | RLIMIT_CPU + a wall clock in *this*    |
|                                          | process (a sleeping script burns no    |
|                                          | CPU, so RLIMIT_CPU alone is not enough)|
| flood stdout to exhaust our memory       | drained by threads, capped, truncated  |
| shell injection through the interpreter  | argv list, never a shell               |

The row that used to read "dropped to an unprivileged uid" is gone, and so is
the `setuid` machinery that implemented it. It required this process to be root,
which the deployment target's Pod Security Standard forbids — and when it could
not run as root it did nothing at all, silently. The container is the boundary
now, which is a boundary the platform enforces rather than one this code asks
for politely.

**The row that stops with the caps is not here.** The query-count cap, the row
cap, the statement timeout and the audit log all live in `client.py`, on the
backend side, along with the database connection. Nothing in this file has ever
held a credential and nothing in it does now — `_pump` forwards query results
between two file objects without parsing them.

**Known gap, stated rather than hidden:** a sidecar in the same Pod shares that
Pod's network namespace, so this still cannot deny the script network egress. A
determined script can reach internal services. Closing it needs a separate Pod
or a NetworkPolicy that would also cut the backend off from Postgres; until
then, the audit log of every run and every statement is the compensating
control.
"""
from __future__ import annotations

import json
import os
import resource
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

from app.sandbox import protocol
from app.sandbox.model import Limits

# Read by *this* process and copied into each run's own directory; the child
# executes the copy and never opens this path. That is what keeps the feature
# independent of how the source tree happens to be mounted or chmodded — see
# _stage.
#
# Derived from the child module's own `__file__` rather than from this file's
# directory: the two no longer live side by side, and a path built from
# `os.path.dirname(__file__)` would point into `app/sandbox/` and find nothing.
from app.services import script_runner_child as _child_module

CHILD = os.path.abspath(_child_module.__file__)

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
# writable disk, a container with nothing in it — never about which modules it
# can name.
SCRIPT_LIBS = os.environ.get("SCRIPT_LIBS_DIR", "/opt/scriptlibs")

# Where each run's private directory is created. Its own volume in the deployed
# form, so a run's staging area is not on the same filesystem as anything else
# and can carry a size limit of its own. Blank means tempfile's default, which
# is what the tests and a checkout use.
WORKDIR = os.environ.get("SCRIPT_SANDBOX_WORKDIR") or None


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
    """Applied in the forked child, before exec.

    There is no uid drop any more — this whole container is the unprivileged
    account the old `setuid` dance was reaching for, so there is nothing left to
    drop *to*. Every call below only ever lowers a limit, which needs no
    privilege, so all of it works exactly as it did.

    One thing improves rather than merely surviving: `RLIMIT_NPROC` was never
    enforced against the old root parent's child at all until the drop happened,
    because the kernel does not apply it to a process holding CAP_SYS_RESOURCE.
    Here nothing holds that capability, so the fork-bomb defence is simply on.

    **`RLIMIT_NPROC` is still counted per uid across the host**, though, and that
    survives the move — it is a property of the kernel, not of who we were. The
    kernel counts tasks (threads included) per uid across the whole user
    namespace, and a container without userns-remap shares that namespace with
    the host. If this container's `runAsUser` collides with something busy
    elsewhere on the node, `execve` fails with EAGAIN — surfacing as
    `BlockingIOError: [Errno 11] ... '/usr/local/bin/python'` before the script
    has run a single line.

    That is why the deployment gives the sandbox a **dedicated uid** rather than
    the obvious `nobody`/65534: 65534 is the uid every unconfigured thing on the
    node lands on, which is exactly the collision this cannot see coming. See
    `app/sandbox/server.py`, which refuses to start as root for the same class of
    reason.

    What the limit costs when it does bite: the child cannot fork at all. The
    fork-bomb defence fails closed — the direction to fail in — instead of taking
    the whole feature down with it.

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
        # told: "terminated by SIGXCPU" instead of "exceeded the time limit".
        # The wall clock owns the message; this stays as the backstop for the case
        # it cannot cover — a child that survives the parent.
        cpu = max(2, limits.wall_clock_s + 2)
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))

    return apply


def _stage(sandbox: str) -> tuple[str, str]:
    """Lay out one run's private directory: an empty cwd, and our own module.

    The child has to be able to *read* the module it is about to execute — and the
    directory that module normally lives in, `/app`, is the one whose permissions
    this process does not control: a bind mount of the host's checkout in
    development, the build context's file modes in the image. A host with a strict
    umask therefore failed every run with `can't open file ... [Errno 13]
    Permission denied`, after a perfectly good exec. Copying the module into the
    run's own directory removes the dependency outright: nothing under `/app`
    needs to be readable by the child, which is also what makes locking `/app`
    down a thing this feature can survive — and what lets a future slim sandbox
    image ship this one file without the application around it.

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

def _close_fds(*fds: int) -> None:
    for fd in fds:
        try:
            os.close(fd)
        except OSError:
            pass


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



def _reap_group(pgid: int) -> None:
    """Reap anything in this run's group that was reparented onto us.

    New in the sidecar, and not optional. In the backend container this process
    was somewhere in the middle of a process tree, and a grandchild orphaned by
    `killpg` was reparented to that container's PID 1, which reaps it. Here the
    supervisor *is* PID 1, so those orphans are reparented to us — and nothing
    waits on them, so they stay zombies.

    That matters because a zombie still answers signal 0. `_survivors` polls
    `killpg(pid, 0)` to decide whether anything is still alive, so without this
    every killed run would report `orphans: 1` — a leak that is not happening,
    reported forever.

    Called after `proc.wait()` has already taken the direct child's status, so it
    cannot race with it, and scoped to this run's group with `-pgid` so two
    concurrent runs cannot steal each other's children.

    Deliberately not a SIGCHLD handler: a global handler would reap the direct
    child before `Popen.wait()` could read its status, and `_exit_reason` — which
    turns the return code into the sentence the user reads — would lose every
    signal it exists to name.
    """
    while True:
        try:
            pid, _ = os.waitpid(-pgid, os.WNOHANG)
        except (ChildProcessError, OSError):
            return
        if pid == 0:
            return


def _pump(source: str, rx, tx, to_child, from_child) -> None:
    """Relay the child's protocol to the backend and its answers back.

    Deliberately plumbing and nothing else. The supervisor forwards `sql`, `ok`
    and `err` lines from the child, and `rows`/`qerr` lines back, **without
    parsing what they carry** — it looks at nothing but the `t` field, and only
    to know whether a line was a verdict. That is what keeps this container free
    of any interest in the data: rows from a business database pass through it as
    bytes it never decodes.

    It is also what leaves `script_runner_child.py` completely unedited by this
    change. The child still gets two pipe file descriptors on argv and still
    speaks exactly the protocol it always did; only the far end of the pipe moved.

    Blocking is safe in both directions. While this waits on `rx` the child is
    computing and its output is drained by the threads started in
    `serve_connection`; while it waits on `from_child` the backend is waiting on
    us. Both are broken by the watchdog's `killpg` (which closes the child's pipe)
    or by the backend closing the socket (which is how a cancel is expressed —
    there is deliberately no `cancel` frame to forget to send).
    """
    try:
        to_child.write(json.dumps({"source": source}) + "\n")
        to_child.flush()
    except (BrokenPipeError, OSError):
        return

    while True:
        try:
            line = from_child.readline()
        except OSError:
            return
        if not line:
            return  # the child died or finished; the caller reports how
        try:
            tx.write(line)
            tx.flush()
        except (BrokenPipeError, OSError):
            return  # the backend went away; teardown kills the group
        if protocol.kind(line) != protocol.SQL:
            return  # a verdict, already forwarded; nothing follows it
        try:
            reply = rx.readline()
        except OSError:
            return
        if not reply:
            return
        try:
            to_child.write(reply)
            to_child.flush()
        except (BrokenPipeError, OSError):
            return


def _send(tx, payload: str) -> None:
    try:
        tx.write(payload + "\n")
        tx.flush()
    except (BrokenPipeError, OSError):
        pass  # the backend hung up; there is nobody left to tell


def serve_connection(conn: socket.socket) -> None:
    """Run one uploaded script, start to finish. One connection is one run.

    Connection-per-run rather than a multiplexed channel with run ids, because
    the connection's lifetime then *is* the run's lifetime: cancellation is a
    close, a crashed backend is an EOF, and there is no table of live runs to
    leak. The backend's own semaphore (`eval_set_scripts.py`) remains the real
    admission gate; `server.py` only keeps a defensive ceiling.
    """
    rx = conn.makefile("r", encoding="utf-8", newline="\n")
    tx = conn.makefile("w", encoding="utf-8", newline="\n")
    try:
        try:
            boot = json.loads(rx.readline() or "null")
        except ValueError:
            boot = None
        if not isinstance(boot, dict) or boot.get("t") != protocol.START:
            _send(tx, protocol.done_frame(stderr="the sandbox was not given a script"))
            return
        source = boot.get("source")
        if not isinstance(source, str):
            _send(tx, protocol.done_frame(stderr="the sandbox was not given a script"))
            return
        _run(source, protocol.limits_from_frame(boot), rx, tx)
    finally:
        for handle in (rx, tx):
            try:
                handle.close()
            except OSError:
                pass
        try:
            conn.close()
        except OSError:
            pass


def _run(source: str, limits: Limits, rx, tx) -> None:
    # An empty, private working directory. The child cannot write to it
    # (RLIMIT_FSIZE), but a process still needs a cwd it is allowed to be in, and
    # this keeps it out of the source tree. Alongside it, out of the script's
    # sight, goes the copy of our own module the child executes — see _stage.
    with tempfile.TemporaryDirectory(prefix="evalscript-", dir=WORKDIR) as sandbox:
        os.chmod(sandbox, 0o755)
        try:
            workdir, child = _stage(sandbox)
        except OSError as exc:
            _send(tx, protocol.done_frame(launch=exc))
            return

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
            )
        except BaseException as exc:
            # Nothing adopted any of the four descriptors, so all four are ours to
            # close — including the two the relay below would have taken over.
            # Closing only the child's half here (the obvious `finally`) leaks two
            # per failed launch, and a feature that fails every time turns that
            # into EMFILE.
            _close_fds(p2c_r, c2p_w, p2c_w, c2p_r)
            if isinstance(exc, OSError):
                # The sandbox failing to start is not a crash to hand the user as
                # a 500 — it is a run that did not happen, and it is reported the
                # same way as every other failed run. The errno travels; the
                # backend turns it into the sentence.
                _send(tx, protocol.done_frame(launch=exc))
                return
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
            _pump(source, rx, tx, to_child, from_child)
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
            # Between wait() and the survivor check, and in that order — see
            # _reap_group for why the check is otherwise wrong here.
            _reap_group(proc.pid)
            orphans = _survivors(proc.pid)

        _send(
            tx,
            protocol.done_frame(
                stdout=streams.get("stdout", ""),
                stderr=streams.get("stderr", ""),
                truncated=bool(
                    streams.get("stdout_truncated") or streams.get("stderr_truncated")
                ),
                timed_out=killed.is_set(),
                orphans=orphans,
                returncode=proc.returncode,
            ),
        )
