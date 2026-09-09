"""The sandbox container's entrypoint: who it refuses to be, and what it binds.

`server.py` carries the guard that replaced the deleted `setuid` machinery. The
old boundary announced itself by existing in the code; the new one is a line in
a deployment manifest, which can be copied forward, edited, or never written at
all. So the process refuses to start when that line is missing, and these tests
are what keep the refusal working — a silent sandbox is not a sandbox.
"""
from __future__ import annotations

import os
import socket
import stat
import sys

import pytest

from app.sandbox import server

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="unix sockets and uids"
)


def test_the_supervisor_refuses_to_run_as_root(monkeypatch, capsys):
    """Running as root gives an uploaded script a privileged parent.

    That is the exact configuration the split exists to make impossible, and it
    is reachable by nothing more than reusing an old manifest. Refusing loudly is
    the whole design: the previous failure mode was that the drop simply did not
    happen and nothing said so.
    """
    monkeypatch.setattr(server.os, "geteuid", lambda: 0)
    with pytest.raises(SystemExit) as raised:
        server.check_uid(allow_root=False)
    assert raised.value.code == 2
    message = capsys.readouterr().err
    assert "runAsUser" in message, "the message must name the thing to fix"


def test_root_is_allowed_only_when_asked_for_explicitly(monkeypatch):
    """The escape hatch is for running this by hand, and it must be deliberate."""
    monkeypatch.setattr(server.os, "geteuid", lambda: 0)
    server.check_uid(allow_root=True)  # must not raise


def test_an_unprivileged_uid_starts_normally(monkeypatch):
    monkeypatch.setattr(server.os, "geteuid", lambda: 10001)
    server.check_uid(allow_root=False)


def test_a_stale_socket_is_replaced_at_bind(tmp_path):
    """A sidecar that was killed leaves its socket file behind; a restart must work."""
    path = str(tmp_path / "sandbox.sock")
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(path)
    stale.close()
    assert os.path.lexists(path)

    sock = server.bind(path)
    try:
        assert stat.S_ISSOCK(os.lstat(path).st_mode)
    finally:
        sock.close()


def test_a_regular_file_at_the_socket_path_is_refused(tmp_path):
    """Only ever unlink something that is actually a socket.

    A regular file here means the volume is not what anyone thinks it is.
    Deleting a file nobody asked us to delete is a worse failure than refusing to
    start, because it is the one that cannot be undone.
    """
    path = tmp_path / "sandbox.sock"
    path.write_text("not a socket")
    with pytest.raises(SystemExit):
        server.bind(str(path))
    assert path.read_text() == "not a socket", "the file must still be there"


def test_the_socket_is_group_reachable_and_closed_to_everyone_else(tmp_path):
    """0660, whatever the umask says.

    The backend and the sandbox run as deliberately different uids, so the socket
    has to be reachable through the group they share — and through nothing else.
    Left to the umask this is a coin flip between "the backend cannot connect"
    and "anything on the node can".
    """
    path = str(tmp_path / "sandbox.sock")
    previous = os.umask(0o077)
    try:
        sock = server.bind(path)
    finally:
        os.umask(previous)
    try:
        mode = stat.S_IMODE(os.lstat(path).st_mode)
        assert mode == 0o660, f"expected 0660, got {mode:04o}"
    finally:
        sock.close()


def test_the_socket_directory_is_created_if_it_is_missing(tmp_path):
    path = str(tmp_path / "run" / "sandbox" / "sandbox.sock")
    sock = server.bind(path)
    try:
        assert stat.S_ISSOCK(os.lstat(path).st_mode)
    finally:
        sock.close()


def test_a_run_over_the_ceiling_is_refused_with_a_readable_reason(tmp_path):
    """Over the ceiling, a connection is answered and closed — never left queued.

    Queueing would be the tempting default, and it turns "the sandbox is busy"
    into a page that hangs for the length of somebody else's ten-minute wall
    clock with nothing to read. Refusing produces the sentence this feature has
    always printed when it could not get a process.
    """
    import json
    import threading

    path = str(tmp_path / "sandbox.sock")
    listener = server.bind(path)
    thread = threading.Thread(target=server.serve, args=(listener, 0), daemon=True)
    thread.start()
    try:
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.settimeout(5)
        conn.connect(path)
        with conn.makefile("r", encoding="utf-8") as rx:
            frame = json.loads(rx.readline())
        conn.close()
    finally:
        listener.close()

    assert frame["t"] == "done"
    assert frame["launch_errno"] is not None


def test_the_healthcheck_reports_a_listener_and_the_lack_of_one(tmp_path):
    path = str(tmp_path / "sandbox.sock")
    assert server._check(path) == 1  # nothing there yet
    sock = server.bind(path)
    try:
        assert server._check(path) == 0
    finally:
        sock.close()


class _FlakySocket:
    """A listening socket whose accept() fails on cue.

    A stub rather than a real socket with a patched method, because
    `socket.accept` is read-only — and because closing a socket does not
    reliably wake a thread already blocked in `accept()`, so a test driven that
    way is testing the platform's scheduling, not this loop.
    """

    def __init__(self, *errnos):
        self.raised = []
        self._queue = list(errnos)

    def accept(self):
        if not self._queue:
            raise AssertionError("the loop asked for more connections than expected")
        number = self._queue.pop(0)
        self.raised.append(number)
        raise OSError(number, os.strerror(number))


def test_a_transient_accept_failure_does_not_end_the_accept_loop():
    """EMFILE is self-clearing; treating it as shutdown takes the sandbox down for good.

    The loop used to return on any OSError from `accept()`, and `main()` then
    exited 0 — so a burst that exhausted the descriptor table left a container
    that had "succeeded" and stopped serving. The base compose file sets no
    restart policy, so nothing would have brought it back, and every script run
    afterwards would have reported that the sandbox was not up.

    The descriptors come back as the runs in flight finish, so the only correct
    response is to wait and ask again.
    """
    import errno

    sock = _FlakySocket(errno.EMFILE, errno.ENFILE, errno.ECONNABORTED, errno.EBADF)
    server.serve(sock, max_runs=2)

    # It kept going through all three transient failures and stopped only on the
    # one that means the socket is gone.
    assert sock.raised == [errno.EMFILE, errno.ENFILE, errno.ECONNABORTED, errno.EBADF]


def test_a_dead_listening_socket_does_end_the_accept_loop():
    """The fix must not turn an unrecoverable socket into an infinite spin."""
    import errno

    sock = _FlakySocket(errno.EBADF)
    server.serve(sock, max_runs=2)
    assert sock.raised == [errno.EBADF]


# --- Packaging: the sandbox must not be handed the CA bundle -----------------
# `backend/certs/` is gitignored, so it is empty on a clean checkout and holds a
# real private-PKI bundle on every machine set up per the README's internal-CA
# instructions. That is precisely the case these guard: the failure only exists
# where there is something to leak, which is the worst place for it to be
# untested. The container test asserts the outcome; these assert the two
# mechanisms that produce it, and run everywhere.

def _backend_dir():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _repo_root():
    return os.path.dirname(_backend_dir())


def test_the_ca_bundle_is_kept_out_of_the_image():
    """`COPY . .` would otherwise bake it in, and the sandbox runs that image.

    The file to read is `backend/.dockerignore`, not the repository root's: the
    backend's build context is `./backend`, and Docker reads the .dockerignore at
    the root of the context and nowhere else. A rule left behind at the old
    location would be silently inert — which is the failure mode worth a test,
    since nothing about the build would announce it.
    """
    path = os.path.join(_backend_dir(), ".dockerignore")
    if not os.path.exists(path):
        pytest.skip(".dockerignore is outside the backend image")
    ignored = open(path, encoding="utf-8").read()
    assert "\ncerts/" in ignored, (
        "certs/ must be excluded from the build context: the image it would "
        "land in is the one the sandbox container runs"
    )


def test_bytecode_rules_are_recursive():
    """The same silent-failure shape as the rule above, one line down.

    A .dockerignore pattern is matched against a file's whole path relative to
    the context root, so `__pycache__/` excludes exactly one directory — the one
    at the top — and every `__pycache__` that actually exists is nested. Dropping
    the `**/` prefix therefore does not fail: it copies one machine's bytecode
    into the image, and into the image the sandbox container runs, while the
    build reports success.
    """
    path = os.path.join(_backend_dir(), ".dockerignore")
    if not os.path.exists(path):
        pytest.skip(".dockerignore is outside the backend image")
    patterns = [
        line.strip()
        for line in open(path, encoding="utf-8")
        if line.strip() and not line.startswith("#")
    ]
    for name in ("__pycache__/", "*.pyc", ".pytest_cache/", ".venv/"):
        assert f"**/{name}" in patterns, (
            f"{name} must be written `**/{name}`: unprefixed, it matches only at "
            "the root of the build context, and every one that matters is nested"
        )


def test_development_masks_the_ca_bundle_in_the_sandbox():
    """The other half of the same rule, for the bind-mounted development stack.

    .dockerignore cannot help here: docker-compose.override.yml mounts the whole
    backend tree — certs included — over /app in both containers.
    """
    path = os.path.join(_repo_root(), "docker-compose.override.yml")
    if not os.path.exists(path):
        pytest.skip("compose files are outside the backend image")
    overlay = open(path, encoding="utf-8").read()
    sandbox_block = overlay.split("\n  sandbox:")[-1]
    assert "/app/certs" in sandbox_block, (
        "the sandbox service must mask /app/certs; the ./backend:/app mount "
        "brings the CA bundle in on any machine configured for the internal CA"
    )
