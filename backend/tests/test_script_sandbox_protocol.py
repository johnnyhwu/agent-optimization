"""What crosses the socket between the backend and the sandbox container.

The containment tests in test_script_sandbox.py assert that the sandbox holds a
script in; these assert the seam it is held in *through*. They exist because the
split moved a function call across a process boundary, and a boundary has
failure modes a call does not: a peer that dies mid-sentence, a verdict that
arrives without its terminator, an error that has to survive being flattened to
an errno and rebuilt on the far side.

The rule the whole file is really about: **a run we cannot account for is not
reported as a clean run.** Everything below is a way of arriving at that.
"""
from __future__ import annotations

import contextlib
import errno
import json
import socket
import sys
import threading

import pytest

from app.sandbox import protocol
from app.sandbox.model import Limits
from app.services.script_runner import run_script

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="the sandbox relies on Linux rlimits and process groups",
)


class FakeExecutor:
    def __init__(self, rows=None):
        self.rows = rows if rows is not None else [{"n": 1}]
        self.calls = []

    def run_sql(self, sql, params):
        self.calls.append((sql, params))
        return list(self.rows)


@contextlib.contextmanager
def _scripted_peer(handler):
    """A transport whose far end is `handler` rather than the real supervisor.

    Lets a test say exactly what the sandbox says back — including things a
    healthy supervisor never says, which is the point: those are the cases the
    backend has to survive and the ones no happy-path test reaches.
    """
    ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    done = threading.Event()

    def serve():
        try:
            rx = theirs.makefile("r", encoding="utf-8", newline="\n")
            tx = theirs.makefile("w", encoding="utf-8", newline="\n")
            handler(rx, tx, theirs)
        finally:
            with contextlib.suppress(OSError):
                theirs.close()
            done.set()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    @contextlib.contextmanager
    def connect(limits):
        tx = ours.makefile("w", encoding="utf-8", newline="\n")
        rx = ours.makefile("r", encoding="utf-8", newline="\n")
        try:
            yield tx, rx
        finally:
            for handle in (tx, rx):
                with contextlib.suppress(OSError):
                    handle.close()

    try:
        yield connect
    finally:
        with contextlib.suppress(OSError):
            ours.close()
        done.wait(timeout=5)


def test_the_start_frame_carries_the_script_and_the_limits_and_nothing_else():
    """The first thing on the wire, checked field by field.

    The sandbox container is deliberately given no credentials, and this is the
    one place the backend could accidentally hand it some anyway. Asserting the
    exact key set — rather than "no password is present" — is what makes a field
    added later a test failure rather than a silent widening.
    """
    seen = {}

    def handler(rx, tx, sock):
        seen["start"] = json.loads(rx.readline())
        tx.write(protocol.done_frame(returncode=0) + "\n")
        tx.flush()

    with _scripted_peer(handler) as connect:
        run_script("def main(h): return []", FakeExecutor(),
                   Limits(max_processes=17, wall_clock_s=11), connect=connect)

    start = seen["start"]
    assert set(start) == {"t", "source", "limits"}
    assert start["t"] == "start"
    assert start["source"] == "def main(h): return []"
    assert start["limits"]["max_processes"] == 17
    assert start["limits"]["wall_clock_s"] == 11


def test_a_supervisor_that_dies_mid_run_is_an_error_not_a_hang():
    """The peer vanishes after the first query. The run must end, and end badly."""

    def handler(rx, tx, sock):
        rx.readline()  # start
        tx.write(json.dumps({"t": "sql", "sql": "SELECT 1", "params": None}) + "\n")
        tx.flush()
        rx.readline()  # our rows; then hang up without a verdict
        sock.close()

    executor = FakeExecutor()
    with _scripted_peer(handler) as connect:
        result = run_script("x", executor, Limits(), connect=connect)

    assert result.value is None
    assert result.error is not None
    assert "stopped answering" in result.error
    assert executor.calls, "the query did happen; only the verdict was lost"


def test_a_verdict_without_its_done_frame_is_not_trusted():
    """`ok` is not the terminator, and a run cut off after one is not a clean run.

    This is the subtle one. The script really did return a value, and the naive
    reading is that we should keep it. But `done` is what says how the process
    ended, what it printed and whether anything survived — so a verdict with no
    `done` behind it is a value with no account of where it came from, and the
    sandbox may have been killed halfway through producing it.
    """

    def handler(rx, tx, sock):
        rx.readline()
        tx.write(json.dumps({"t": "ok", "value": [{"question": "q"}]}) + "\n")
        tx.flush()
        sock.close()

    with _scripted_peer(handler) as connect:
        result = run_script("x", FakeExecutor(), Limits(), connect=connect)

    assert result.value is None
    assert result.error is not None


def test_a_verdict_and_its_done_frame_make_one_result():
    """The ordinary case: `ok` then `done`, combined into a single RunResult."""

    def handler(rx, tx, sock):
        rx.readline()
        tx.write(json.dumps({"t": "ok", "value": [{"question": "q"}]}) + "\n")
        tx.write(protocol.done_frame(stdout="hello\n", returncode=0) + "\n")
        tx.flush()

    with _scripted_peer(handler) as connect:
        result = run_script("x", FakeExecutor(), Limits(), connect=connect)

    assert result.error is None
    assert result.value == [{"question": "q"}]
    assert result.stdout == "hello\n"


def test_an_eagain_from_the_sandbox_keeps_todays_wording():
    """A launch failure crosses the socket as an errno and comes back as prose.

    The paragraph is the one this feature has always printed. Flattening the
    exception to `errno` and rebuilding it on this side is what keeps it that
    way, and `"11" not in error` is the specific regression: an errno is not a
    thing to show somebody who uploaded a file.
    """

    def handler(rx, tx, sock):
        rx.readline()
        tx.write(
            protocol.done_frame(launch=OSError(errno.EAGAIN, "Resource temporarily unavailable"))
            + "\n"
        )
        tx.flush()

    with _scripted_peer(handler) as connect:
        result = run_script("x", FakeExecutor(), Limits(), connect=connect)

    assert result.error is not None
    assert "could not be started" in result.error
    assert "no process slots" in result.error
    assert "11" not in result.error


def test_the_sandbox_is_never_sent_rows_after_the_verdict():
    """Nothing is written to the socket once a verdict has been seen."""
    written = []

    def handler(rx, tx, sock):
        rx.readline()
        tx.write(json.dumps({"t": "ok", "value": []}) + "\n")
        tx.write(protocol.done_frame(returncode=0) + "\n")
        tx.flush()
        # Anything further from the backend would arrive here.
        sock.settimeout(0.5)
        try:
            written.append(rx.readline())
        except (OSError, socket.timeout):
            pass

    with _scripted_peer(handler) as connect:
        run_script("x", FakeExecutor(), Limits(), connect=connect)

    assert written in ([], [""]), f"the backend kept talking: {written}"


def test_the_supervisor_clamps_limits_it_is_handed():
    """The sandbox honours its own ceilings, not whatever arrives on the wire.

    Not a trust boundary in the way the child's is — only the pod can reach the
    socket — but a supervisor that applies any number it is given is one backend
    bug away from applying none.
    """
    clamped = protocol.limits_from_frame(
        {"limits": {"memory_mb": 10 ** 9, "wall_clock_s": 10 ** 6, "max_processes": -4}}
    )
    assert clamped.memory_mb == 8192
    assert clamped.wall_clock_s == 3600
    assert clamped.max_processes == Limits().max_processes  # negative → the default


def test_an_unknown_limit_field_does_not_fail_the_run():
    """A backend one deploy ahead of its sidecar degrades, it does not break."""
    limits = protocol.limits_from_frame({"limits": {"something_new": 5, "max_queries": 9}})
    assert limits.max_queries == 9


# --- A lost run must never read as a bad script ------------------------------
# Every one of these was a real defect: the backend returned a RunResult with
# neither a value nor an error, which reads three layers up as "main() returned
# None". The sandbox dying was therefore reported to the person who uploaded the
# file as a bug in their code, and written to the audit log as a clean run.

def test_a_socket_that_breaks_while_sending_the_script_is_reported():
    def handler(rx, tx, sock):
        sock.close()  # gone before the start frame lands

    with _scripted_peer(handler) as connect:
        result = run_script("def main(h): return []", FakeExecutor(), Limits(), connect=connect)

    assert result.value is None
    assert result.error is not None
    assert "run was lost" in result.error


def test_a_socket_that_breaks_while_returning_rows_is_reported():
    """The sandbox asks a query, then vanishes before it can be answered."""

    def handler(rx, tx, sock):
        rx.readline()  # start
        tx.write(json.dumps({"t": "sql", "sql": "SELECT 1", "params": None}) + "\n")
        tx.flush()
        sock.close()

    executor = FakeExecutor()
    with _scripted_peer(handler) as connect:
        result = run_script("x", executor, Limits(), connect=connect)

    assert result.value is None
    assert result.error is not None
    assert "run was lost" in result.error


def test_a_lost_run_is_never_an_empty_success():
    """The property behind all of the above, stated once.

    `_execute` in the router turns `value=None, error=None` into a complaint
    about the script's return type, and `_audit` logs it as outcome=ok. So a
    RunResult that carries neither is not merely unhelpful — it is actively
    wrong twice, and it is the shape a container boundary produces that a
    function call never did.
    """

    def handler(rx, tx, sock):
        rx.readline()
        sock.close()

    with _scripted_peer(handler) as connect:
        result = run_script("x", FakeExecutor(), Limits(), connect=connect)

    assert not (result.value is None and result.error is None), (
        "a lost run must carry an error; without one it is reported as a bad script"
    )


def test_a_supervisor_side_enoent_is_not_blamed_on_the_service_being_down():
    """The same errno, two very different call-outs.

    `ENOENT` from the transport means the sandbox container is not running.
    `ENOENT` forwarded from a supervisor that *is* running means something
    inside it failed — a missing staging volume, an interpreter that would not
    exec. Telling an operator to go check a service that is up, while the real
    fault is inside it, is the kind of message that costs an hour.
    """
    import errno as _errno

    def handler(rx, tx, sock):
        rx.readline()
        tx.write(
            protocol.done_frame(launch=OSError(_errno.ENOENT, "No such file or directory"))
            + "\n"
        )
        tx.flush()

    with _scripted_peer(handler) as connect:
        result = run_script("x", FakeExecutor(), Limits(), connect=connect)

    assert result.error is not None
    assert "could not be started" in result.error
    assert "not answering" not in result.error, "this sandbox is up; do not say it is down"
    assert "is not up" not in result.error
