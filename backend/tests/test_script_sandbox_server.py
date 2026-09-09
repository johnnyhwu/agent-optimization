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
