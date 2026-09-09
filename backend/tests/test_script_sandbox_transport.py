"""Reaching the sandbox container, and what happens when it is not there.

The failure this file exists to prevent is not a crash. It is the *other* one:
a backend that cannot reach the sandbox and quietly runs the script itself, next
to the credentials, which is precisely the arrangement the split removed. There
is no fallback path to test, so what is tested is that every way of not reaching
the sandbox ends as a failed run with a sentence.
"""
from __future__ import annotations

import os
import socket
import sys

import pytest

from app.config import Settings
from app.sandbox import transport
from app.sandbox.model import Limits
from app.services.script_runner import run_script

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="the sandbox relies on Linux rlimits and process groups",
)


class FakeExecutor:
    def run_sql(self, sql, params):
        return [{"n": 1}]


def _sidecar_at(path, timeout_s=0.3):
    def connect(limits):
        return transport.sidecar(limits, path=path, connect_timeout_s=timeout_s)

    return connect


def test_the_production_default_transport_is_the_sidecar():
    """The guard on the autouse fixture in conftest.

    The suite runs the supervisor in-process, which means every other test in the
    repository would keep passing if somebody changed the shipped default to
    "inprocess" — and the deployment would then execute uploaded scripts inside
    the backend container with all of its secrets, silently. A fresh Settings
    instance is the only place that can be checked.
    """
    assert Settings().script_sandbox_transport == "sidecar"


def test_a_missing_socket_is_a_failed_run_not_a_500(tmp_path):
    result = run_script(
        "def main(h): return []",
        FakeExecutor(),
        Limits(),
        connect=_sidecar_at(str(tmp_path / "absent.sock")),
    )
    assert result.value is None
    assert result.error is not None
    assert "could not be started" in result.error
    assert "not answering" in result.error


def test_a_socket_nobody_is_listening_on_is_a_failed_run(tmp_path):
    """A stale socket file left by a crashed sidecar: ECONNREFUSED, not a hang."""
    path = str(tmp_path / "stale.sock")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(path)  # bound but never listen()ing
    try:
        result = run_script("def main(h): return []", FakeExecutor(), Limits(),
                            connect=_sidecar_at(path))
    finally:
        sock.close()
    assert result.error is not None
    assert "could not be started" in result.error


def test_a_slow_sidecar_is_waited_for_then_reported(tmp_path):
    """The retry window is real, and it ends.

    A run that lands while the sidecar is restarting should succeed; one that
    lands while it is gone should not wait forever. Both are the same code path
    with a different deadline, so the assertion is on the deadline being honoured.
    """
    import time

    started = time.monotonic()
    result = run_script("def main(h): return []", FakeExecutor(), Limits(),
                        connect=_sidecar_at(str(tmp_path / "never.sock"), timeout_s=0.6))
    elapsed = time.monotonic() - started
    assert result.error is not None
    assert 0.5 <= elapsed < 5, f"retry window not honoured: {elapsed:.2f}s"


def test_the_in_process_transport_leaves_no_descriptors_behind():
    """Twenty-five runs, and the descriptor count must come back to where it was.

    The in-process transport owns a socketpair and a thread per run. This is the
    test that notices when it forgets one — and a leak here is invisible until a
    long-lived backend hits EMFILE, which is the expensive kind of invisible.
    """
    def count():
        return len(os.listdir("/proc/self/fd"))

    run_script("def main(h): return []", FakeExecutor(), Limits())  # warm up
    before = count()
    for _ in range(25):
        run_script("def main(h): return [{'question': 'q'}]", FakeExecutor(), Limits())
    assert count() <= before + 2, f"descriptors leaked: {before} -> {count()}"


def test_two_concurrent_runs_do_not_see_each_other():
    """Each run gets its own connection, its own child and its own staging dir."""
    import threading

    results = {}

    def go(name, src):
        results[name] = run_script(src, FakeExecutor(), Limits(wall_clock_s=30))

    threads = [
        threading.Thread(target=go, args=("a", "def main(h): return [{'question': 'a'}]")),
        threading.Thread(target=go, args=("b", "def main(h): return [{'question': 'b'}]")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert results["a"].value == [{"question": "a"}]
    assert results["b"].value == [{"question": "b"}]


def test_a_timeout_in_one_run_leaves_the_other_alone():
    """One run hitting the wall clock must not take its neighbour down with it."""
    import threading

    results = {}

    def slow():
        results["slow"] = run_script(
            "import time\ndef main(h):\n    time.sleep(30)\n    return []",
            FakeExecutor(), Limits(wall_clock_s=2),
        )

    def quick():
        results["quick"] = run_script(
            "def main(h): return [{'question': 'q'}]", FakeExecutor(), Limits(wall_clock_s=30)
        )

    threads = [threading.Thread(target=slow), threading.Thread(target=quick)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert results["slow"].timed_out is True
    assert results["quick"].error is None
    assert results["quick"].value == [{"question": "q"}]
    assert results["slow"].orphans == 0


def test_an_absent_sandbox_raises_the_type_that_names_it(tmp_path):
    """The distinction `launch_reason` relies on is carried by the type.

    If this ever becomes a plain OSError again, the "sandbox is not up" message
    silently starts firing for failures that happen *inside* a running sandbox.
    Nothing else would fail, which is why it is asserted here directly.
    """
    from app.sandbox.model import SandboxUnavailable

    with pytest.raises(SandboxUnavailable):
        with transport.sidecar(Limits(), path=str(tmp_path / "absent.sock"),
                               connect_timeout_s=0.2):
            pass
