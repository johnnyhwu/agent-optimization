"""The sandbox sidecar's entrypoint: `python -m app.sandbox.server`.

Binds a unix socket, accepts one connection per run, and hands each to
`supervisor.serve_connection` on its own thread. Nothing else — the admission
control that matters is the backend's semaphore; the ceiling here is defensive.

The guard worth reading is `check_uid`. Deleting the old `setuid` machinery
removed a boundary that *announced itself by existing*; what replaced it is a
property of the deployment manifest, which is a thing that can be copied
forward, edited, or simply never written. A sandbox running as root is a sandbox
that has quietly stopped being one, so this refuses to start instead — the whole
point of the change is to convert a silent degradation into a loud one.
"""
from __future__ import annotations

import argparse
import errno
import os
import socket
import stat
import sys
import threading
import time

from app.sandbox import protocol, supervisor

DEFAULT_SOCKET = "/run/sandbox/sandbox.sock"

# accept() failures that mean "try again", not "stop serving". EMFILE/ENFILE
# clear as running scripts release their descriptors; ECONNABORTED is a peer
# that gave up between the SYN and the accept; EINTR is a signal.
_TRANSIENT_ACCEPT_ERRORS = frozenset(
    {errno.EMFILE, errno.ENFILE, errno.ECONNABORTED, errno.EINTR, errno.EAGAIN}
)


def check_uid(allow_root: bool = False) -> None:
    """Refuse to run as root unless explicitly told otherwise.

    The escape hatch exists for a developer running the module directly on a
    machine where they happen to be root, not for a deployment. It is an
    environment variable rather than a flag so that nothing in a manifest's
    `command:` can set it by accident.
    """
    if os.geteuid() != 0 or allow_root:
        return
    sys.stderr.write(
        "app.sandbox.server refuses to run as root.\n"
        "\n"
        "This process execs uploaded scripts. Running it as root gives them a\n"
        "privileged parent and undoes the isolation the sandbox container exists\n"
        "to provide. Give the container its own unprivileged account — in\n"
        "Kubernetes `securityContext.runAsUser`, in compose `user:` — and prefer\n"
        "a dedicated uid over 65534/nobody (see _preexec in supervisor.py for\n"
        "why RLIMIT_NPROC makes that collision expensive).\n"
        "\n"
        "Set SCRIPT_SANDBOX_ALLOW_ROOT=1 only to run this by hand.\n"
    )
    raise SystemExit(2)


def bind(path: str, mode: int = 0o660) -> socket.socket:
    """Bind the listening socket, replacing a stale one from a previous start.

    Only ever unlinks something that is actually a socket. A regular file at this
    path means the mount is not what anyone thinks it is, and deleting a file
    nobody asked us to delete is a worse failure than refusing to start.

    The mode is set rather than left to the umask: the backend and the sandbox
    run as *different* uids by design, so the socket has to be reachable by the
    group they share and by nobody else.
    """
    if os.path.lexists(path):
        if not stat.S_ISSOCK(os.lstat(path).st_mode):
            raise SystemExit(f"{path} exists and is not a socket; refusing to replace it")
        os.unlink(path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(path)
    os.chmod(path, mode)
    sock.listen(64)
    return sock


def serve(sock: socket.socket, max_runs: int) -> None:
    """Accept forever, one thread per run, with a ceiling.

    Over the ceiling a connection is accepted and immediately answered with a
    `done` frame carrying EAGAIN, rather than left queued in the listen backlog.
    The difference matters to whoever pressed the button: refused turns into
    today's "no process slots" sentence, whereas queued turns into a page that
    hangs for the length of somebody else's wall clock with nothing to read.
    """
    live = threading.Semaphore(max_runs)
    while True:
        try:
            conn, _ = sock.accept()
        except OSError as exc:
            # Not every OSError here is a shutdown, and treating them alike is
            # how a sandbox stops serving without anyone noticing. Running out
            # of descriptors (EMFILE/ENFILE) is transient and self-clearing —
            # the runs in flight will return theirs — but it would otherwise
            # end this loop, exit 0, and leave the container "successfully"
            # dead: the base compose file sets no restart policy, so nothing
            # would bring it back, and every script run afterwards would report
            # that the sandbox is not up.
            if exc.errno in _TRANSIENT_ACCEPT_ERRORS:
                time.sleep(0.1)
                continue
            # EBADF/EINVAL and anything else: the socket is not coming back,
            # so spinning on it would be worse than stopping.
            #
            # Note this is not how the container shuts down — that is SIGTERM,
            # which never reaches this branch. Closing the listening socket does
            # not reliably wake a thread already blocked in accept(), so nothing
            # should be built on the assumption that it does.
            return
        if not live.acquire(blocking=False):
            _refuse(conn)
            continue

        def run(conn=conn):
            try:
                supervisor.serve_connection(conn)
            finally:
                live.release()

        threading.Thread(target=run, daemon=True).start()


def _refuse(conn: socket.socket) -> None:
    try:
        with conn.makefile("w", encoding="utf-8", newline="\n") as tx:
            tx.write(
                protocol.done_frame(
                    launch=OSError(errno.EAGAIN, "the sandbox is at its concurrent run limit")
                )
                + "\n"
            )
            tx.flush()
    except OSError:
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass


def _check(path: str) -> int:
    """Connect and hang up. The container healthcheck: is anyone listening?"""
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.settimeout(3)
    try:
        conn.connect(path)
    except OSError as exc:
        sys.stderr.write(f"{path}: {exc}\n")
        return 1
    finally:
        conn.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.sandbox.server")
    parser.add_argument("--socket", default=os.environ.get("SCRIPT_SANDBOX_SOCKET", DEFAULT_SOCKET))
    parser.add_argument(
        "--max-runs",
        type=int,
        default=int(os.environ.get("SCRIPT_SANDBOX_MAX_RUNS", "0")) or None,
    )
    parser.add_argument("--check", action="store_true", help="probe the socket and exit")
    args = parser.parse_args(argv)

    if args.check:
        return _check(args.socket)

    check_uid(os.environ.get("SCRIPT_SANDBOX_ALLOW_ROOT") == "1")
    # Twice the backend's own gate, so this only ever bites when something has
    # gone wrong upstream — a second backend replica, or a semaphore that leaked.
    max_runs = args.max_runs or 2 * int(os.environ.get("SCRIPT_MAX_CONCURRENT_RUNS", "2"))
    sock = bind(args.socket)
    sys.stderr.write(
        f"sandbox supervisor listening on {args.socket} "
        f"as uid {os.geteuid()}, up to {max_runs} concurrent runs\n"
    )
    sys.stderr.flush()
    try:
        serve(sock, max_runs)
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
