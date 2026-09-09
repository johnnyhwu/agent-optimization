"""How the backend reaches the supervisor — and how the tests reach it too.

Two implementations of one seam:

    sidecar      connect to the supervisor's unix socket. The deployed form.
    in_process   a socketpair with `supervisor.serve_connection` on a thread.

`in_process` is not a mock. It is the production supervisor, driven over a real
socket, forking a real child under real rlimits and killing it with a real
`killpg` — everything the containment tests assert on. What it gives up is the
container boundary itself: a different uid, an environment with no secrets in
it, a `/proc` with nothing to find. No pytest process can create those, so they
are asserted against a running stack instead (see the sandbox_container tests).

**There is deliberately no fallback between them.** A missing socket is a failed
run with a sentence, never a quiet demotion to running the script inside the
backend container — that demotion is the exact failure this whole split exists
to remove, and it is the kind that shows up as nothing at all.
"""
from __future__ import annotations

import contextlib
import errno
import socket
import threading
import time

from app.sandbox.model import Limits

# How long a run may sit on the socket beyond its own wall clock before the
# backend gives up on the sidecar. The supervisor owns the real deadline and
# kills the child itself; this exists only so that a sidecar which has died or
# wedged cannot hold a worker thread forever.
SOCKET_GRACE_S = 15


@contextlib.contextmanager
def in_process(limits: Limits):
    """Run the supervisor on a thread in this process, over a socketpair.

    Closes both ends and joins the thread on the way out. That is not tidiness:
    `test_a_failed_launch_does_not_leak_file_descriptors` counts `/proc/self/fd`
    across twenty-five failed launches, so anything this forgets shows up there
    as a flake. Treat that test as this function's regression test.
    """
    # Imported here rather than at module scope so that a backend which only ever
    # uses the sidecar transport does not pull the process-spawning half of the
    # sandbox into its address space.
    from app.sandbox import supervisor

    ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    thread = threading.Thread(
        target=supervisor.serve_connection, args=(theirs,), daemon=True
    )
    thread.start()
    ours.settimeout(limits.wall_clock_s + SOCKET_GRACE_S)
    tx = ours.makefile("w", encoding="utf-8", newline="\n")
    rx = ours.makefile("r", encoding="utf-8", newline="\n")
    try:
        yield tx, rx
    finally:
        for handle in (tx, rx):
            try:
                handle.close()
            except OSError:
                pass
        try:
            ours.close()
        except OSError:
            pass
        # The supervisor sees EOF and tears its run down; it should be prompt,
        # but a hung one must not wedge the request thread as well.
        thread.join(timeout=SOCKET_GRACE_S)


@contextlib.contextmanager
def sidecar(limits: Limits, path: str | None = None, connect_timeout_s: float | None = None):
    """Connect to the supervisor's unix socket, retrying briefly while it starts.

    The retry covers exactly two ordinary situations and nothing else: the first
    run after the stack comes up, where compose's start ordering is looser than
    it looks, and a run that lands in the gap while the sidecar is restarting.
    `FileNotFoundError` and `ConnectionRefusedError` only — anything else is a
    real failure and is reported immediately rather than waited out.
    """
    from app.config import settings

    path = path or settings.script_sandbox_socket
    deadline = time.monotonic() + (
        connect_timeout_s
        if connect_timeout_s is not None
        else settings.script_sandbox_connect_timeout_s
    )
    while True:
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            conn.settimeout(limits.wall_clock_s + SOCKET_GRACE_S)
            conn.connect(path)
            break
        except (FileNotFoundError, ConnectionRefusedError) as exc:
            conn.close()
            if time.monotonic() >= deadline:
                # Reported as an OSError so it reaches `launch_reason` like every
                # other way the sandbox can fail to start.
                raise OSError(
                    exc.errno or errno.ENOENT,
                    f"the sandbox did not answer on {path}",
                ) from None
            time.sleep(0.2)
        except BaseException:
            conn.close()
            raise

    tx = conn.makefile("w", encoding="utf-8", newline="\n")
    rx = conn.makefile("r", encoding="utf-8", newline="\n")
    try:
        yield tx, rx
    finally:
        for handle in (tx, rx):
            try:
                handle.close()
            except OSError:
                pass
        try:
            conn.close()
        except OSError:
            pass


def default_transport():
    """Which seam this process uses, from settings.

    Settings rather than an argument to `run_script`, because the tests that need
    the in-process form drive the sandbox through production call paths — the
    HTTP endpoint, `script_executor`'s round trip — that have nowhere to thread a
    parameter through. One switch, set once by a fixture, reaches all of them.
    """
    from app.config import settings

    if settings.script_sandbox_transport == "inprocess":
        return in_process
    return sidecar
