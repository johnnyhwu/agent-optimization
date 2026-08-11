"""Containment tests for the uploaded-script runner (services/script_runner).

These are the most important tests in this feature and the ones most likely to
rot silently: nothing in normal use exercises them, so a refactor that quietly
drops a resource limit or leaks the environment would otherwise ship unnoticed.
Each test here corresponds to a row of the threat table in the module docstring
of `script_runner.py`; if you remove a defence, remove its test in the same
commit and say why.

The load-bearing design property under test throughout: **the child process never
holds database credentials.** `run_sql` is an RPC back to this process, so the
read-only rule, the row cap and the query cap are enforced somewhere the script
cannot reach.
"""
from __future__ import annotations

import errno
import os
import subprocess
import sys
import textwrap

import pytest

from app.services import script_runner
from app.services.script_runner import Limits, QueryError, _runner_uid, run_script

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="the sandbox relies on Linux rlimits and process groups",
)


class FakeExecutor:
    """Stands in for the Postgres executor. Records what the script asked for."""

    def __init__(self, rows=None, raises=None):
        self.rows = rows if rows is not None else [{"n": 1}]
        self.raises = raises
        self.calls = []

    def run_sql(self, sql, params):
        self.calls.append((sql, params))
        if self.raises:
            raise self.raises
        return list(self.rows)


def run(src, executor=None, **limit_overrides):
    limits = Limits(**limit_overrides)
    return run_script(textwrap.dedent(src), executor or FakeExecutor(), limits)


# --- The happy path, so the failures below mean something --------------------

def test_returns_the_value_main_produced():
    result = run("""
        def main(database_handler):
            return [{"question": "q"}]
    """)
    assert result.error is None
    assert result.value == [{"question": "q"}]
    assert result.duration_ms >= 0


def test_run_sql_reaches_the_parent_and_rows_come_back():
    ex = FakeExecutor(rows=[{"a": 1}, {"a": 2}])
    result = run("""
        def main(database_handler):
            return database_handler.run_sql("SELECT a FROM t WHERE x = %s", ("v",))
    """, ex)
    assert result.error is None
    assert result.value == [{"a": 1}, {"a": 2}]
    assert ex.calls == [("SELECT a FROM t WHERE x = %s", ["v"])]


def test_run_sql_params_are_optional():
    ex = FakeExecutor()
    run("""
        def main(database_handler):
            return database_handler.run_sql("SELECT 1")
    """, ex)
    assert ex.calls == [("SELECT 1", None)]


def test_named_params_survive_the_round_trip():
    ex = FakeExecutor()
    run("""
        def main(database_handler):
            return database_handler.run_sql("SELECT %(t)s", {"t": "billing"})
    """, ex)
    assert ex.calls == [("SELECT %(t)s", {"t": "billing"})]


def test_async_main_is_awaited():
    result = run("""
        async def main(database_handler):
            return [{"ok": True}]
    """)
    assert result.error is None
    assert result.value == [{"ok": True}]


def test_non_json_scalars_from_the_database_are_stringified_not_fatal():
    # Real rows carry Decimal/datetime/UUID. Losing the whole run to a
    # serialization error would be a bad first experience.
    from datetime import date
    from decimal import Decimal

    ex = FakeExecutor(rows=[{"d": date(2026, 1, 2), "amount": Decimal("42.50")}])
    result = run("""
        def main(database_handler):
            rows = database_handler.run_sql("SELECT 1")
            return [{"question": str(rows[0]["d"]), "amount": rows[0]["amount"]}]
    """, ex)
    assert result.error is None
    assert result.value == [{"question": "2026-01-02", "amount": "42.50"}]


# --- Containment -------------------------------------------------------------

def test_the_child_environment_carries_no_application_secret(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-must-not-leak")
    monkeypatch.setenv("DATABASE_URL", "postgresql://agentopt:hunter2@db/agentopt")
    result = run("""
        import os
        def main(database_handler):
            return [{"env": sorted(os.environ)}]
    """)
    assert result.error is None
    names = result.value[0]["env"]
    assert "OPENAI_API_KEY" not in names
    assert "DATABASE_URL" not in names
    # Whatever survives, none of it may contain a secret value.
    joined = repr(result.value)
    assert "sk-must-not-leak" not in joined
    assert "hunter2" not in joined


def test_database_credentials_never_enter_the_child():
    # The strongest statement this suite makes: even a script that dumps every
    # variable it can reach finds no password, because the connection lives here.
    ex = FakeExecutor()
    result = run("""
        import os
        def main(database_handler):
            seen = repr(os.environ) + repr(vars(database_handler))
            seen += repr(getattr(database_handler, "__dict__", {}))
            return [{"seen": seen}]
    """, ex)
    assert result.error is None
    assert "password" not in result.value[0]["seen"].lower()


def test_an_infinite_loop_is_killed_at_the_wall_clock_limit():
    result = run("""
        def main(database_handler):
            while True:
                pass
    """, wall_clock_s=2)
    assert result.timed_out
    assert result.value is None
    assert "2" in result.error and "second" in result.error.lower()


def test_a_sleeping_script_is_killed_too():
    # RLIMIT_CPU alone would never fire here — a sleeping process burns no CPU.
    # This is what the parent-side wall clock is for.
    result = run("""
        import time
        def main(database_handler):
            time.sleep(60)
    """, wall_clock_s=2)
    assert result.timed_out


def test_a_killed_script_leaves_no_process_behind():
    result = run("""
        import subprocess, sys, time
        def main(database_handler):
            subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            time.sleep(60)
    """, wall_clock_s=2)
    assert result.timed_out
    # The whole process group is signalled, so nothing outlives the run.
    assert result.orphans == 0


def test_writing_a_file_is_refused(tmp_path):
    # RLIMIT_FSIZE caps what may be *written*, not what may be created: an empty
    # file still appears. That is the honest boundary — nothing the script writes
    # can reach the disk, and it cannot fill the volume — so the assertion is
    # about content, not existence.
    target = tmp_path / "exfiltrated.txt"
    target.parent.chmod(0o777)
    result = run(f"""
        def main(database_handler):
            with open({str(target)!r}, "w") as fh:
                fh.write("x" * 4096)
            return [{{"ok": 1}}]
    """)
    assert result.error is not None
    assert result.value is None
    assert not target.exists() or target.stat().st_size == 0


def test_a_fork_bomb_does_not_escape():
    result = run("""
        import os
        def main(database_handler):
            for _ in range(10000):
                os.fork()
            return []
    """, wall_clock_s=5)
    # Either the fork is refused or the run is killed; what must not happen is a
    # successful return, and the parent must survive to report it.
    assert result.error is not None


def test_the_child_is_unprivileged_and_carries_its_limits():
    """Both halves of the containment still arrive, and in the right order.

    The uid drop is done by Popen and the limits by preexec_fn, which run at
    different points in the child; asserting them from inside the script is what
    catches either one silently going missing.
    """
    result = run("""
        import os, resource
        def main(database_handler):
            return [{
                "uid": os.getuid(),
                "nproc": resource.getrlimit(resource.RLIMIT_NPROC),
                "fsize": resource.getrlimit(resource.RLIMIT_FSIZE),
            }]
    """, max_processes=32)
    assert result.error is None
    seen = result.value[0]
    assert seen["nproc"] == [32, 32]
    assert seen["fsize"] == [0, 0]
    if _runner_uid() is not None:
        # Privileged enough to drop: the script must not be running as us.
        assert seen["uid"] != os.getuid()
        assert seen["uid"] != 0


def test_a_source_tree_the_runner_uid_cannot_read_still_runs(tmp_path, monkeypatch):
    """Regression: the child must not depend on /app being readable.

    The child is dropped to another uid, so exec'ing our module straight out of
    /app required that uid to be able to read it — and /app's permissions come
    from the host (a bind mount in development, the build context's file modes in
    the image), not from anything this code controls. A strict umask on the host
    therefore failed every run with `can't open file ... Permission denied`, right
    after a successful exec. Here the module's directory is closed to everyone but
    us, exactly as it was on the host that reported it, and the run must work.
    """
    if _runner_uid() is None:
        pytest.skip("unprivileged: the child is not dropped to another uid here")

    private = tmp_path / "private"
    private.mkdir()
    hidden = private / os.path.basename(script_runner.CHILD)
    hidden.write_bytes(open(script_runner.CHILD, "rb").read())
    hidden.chmod(0o600)
    private.chmod(0o700)  # ours to read; unreadable to the uid the child runs as
    monkeypatch.setattr(script_runner, "CHILD", str(hidden))

    result = run("""
        def main(database_handler):
            return [{"question": "q"}]
    """)
    assert result.error is None
    assert result.value == [{"question": "q"}]


def test_the_script_working_directory_is_empty():
    """The copied module goes beside the cwd, not in it.

    An empty working directory is one of the stated properties of the sandbox;
    dropping our own module into it would quietly retire that, and hand the script
    a file to read for no reason.
    """
    result = run("""
        import os
        def main(database_handler):
            return [{"entries": sorted(os.listdir("."))}]
    """)
    assert result.error is None
    assert result.value[0]["entries"] == []


def test_a_sandbox_that_cannot_be_laid_out_is_a_result_not_an_exception(monkeypatch):
    def refuse(*args, **kwargs):
        raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))

    monkeypatch.setattr(script_runner.shutil, "copyfile", refuse)

    result = run("""
        def main(database_handler):
            return []
    """)
    assert result.value is None
    assert result.error is not None
    assert "could not be started" in result.error


def test_a_busy_runner_uid_still_lets_the_sandbox_start():
    """Regression: the fork-bomb limit must not stop the sandbox from starting.

    RLIMIT_NPROC counts tasks per uid across the whole user namespace, which a
    container shares with its host — so the uid the child is dropped to is
    routinely busy for reasons this container cannot see. Applying the limit
    *before* the uid drop made the kernel fail the following `execve` with EAGAIN
    (`BlockingIOError: [Errno 11] ... '/usr/local/bin/python'`) on every single
    run, before the script executed a line. Here the runner uid is deliberately
    put over its limit and the run must still work.
    """
    credentials = _runner_uid()
    if credentials is None:
        pytest.skip("unprivileged: the child is not dropped to another uid here")
    uid, gid = credentials

    busy = [
        subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            user=uid,
            group=gid,
            extra_groups=[],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(8)
    ]
    try:
        result = run("""
            def main(database_handler):
                return [{"question": "q"}]
        """, max_processes=4)
    finally:
        for proc in busy:
            proc.kill()
            proc.wait()

    assert result.error is None
    assert result.value == [{"question": "q"}]


def test_a_sandbox_that_cannot_start_is_a_result_not_an_exception(monkeypatch):
    """A failed launch is a failed run, not a 500.

    The endpoint's contract is that a run that did not work comes back as a 200
    carrying `error`, the checklist and whatever output there was. An OSError
    escaping this function skips all of that.
    """

    def refuse(*args, **kwargs):
        raise BlockingIOError(errno.EAGAIN, os.strerror(errno.EAGAIN), sys.executable)

    monkeypatch.setattr(script_runner.subprocess, "Popen", refuse)

    result = run("""
        def main(database_handler):
            return []
    """)
    assert result.value is None
    assert result.error is not None
    assert "could not be started" in result.error
    # The message names what the user can do about it, not the errno.
    assert "11" not in result.error


def test_a_failed_launch_does_not_leak_file_descriptors(monkeypatch):
    """Four descriptors are opened before Popen; a failure must close all four.

    Closing only the child's half — the shape a plain `finally` gives you — leaks
    two per attempt, so a feature that fails every time degrades from a readable
    error into EMFILE.
    """

    def refuse(*args, **kwargs):
        raise BlockingIOError(errno.EAGAIN, os.strerror(errno.EAGAIN), sys.executable)

    monkeypatch.setattr(script_runner.subprocess, "Popen", refuse)

    def open_fds():
        return len(os.listdir("/proc/self/fd"))

    run("def main(database_handler):\n    return []\n")  # warm up any lazy imports
    before = open_fds()
    for _ in range(25):
        run("def main(database_handler):\n    return []\n")
    assert open_fds() == before


def test_memory_hog_is_stopped():
    result = run("""
        def main(database_handler):
            blob = bytearray(4 * 1024 * 1024 * 1024)
            return [{"n": len(blob)}]
    """, memory_mb=256, wall_clock_s=10)
    assert result.error is not None
    assert result.value is None


# --- Output channels ---------------------------------------------------------

def test_print_reaches_the_user_and_does_not_corrupt_the_protocol():
    # stdout belongs to the script; the RPC runs on its own file descriptors.
    # If those were shared, this test would fail by breaking the run entirely.
    ex = FakeExecutor()
    result = run("""
        import sys
        def main(database_handler):
            print("counting rows")
            database_handler.run_sql("SELECT 1")
            print("done")
            print("a warning", file=sys.stderr)
            return [{"ok": 1}]
    """, ex)
    assert result.error is None
    assert result.value == [{"ok": 1}]
    assert "counting rows" in result.stdout
    assert "done" in result.stdout
    assert "a warning" in result.stderr


def test_a_flood_of_output_is_truncated_rather_than_buffered_without_limit():
    result = run("""
        def main(database_handler):
            for _ in range(200000):
                print("x" * 100)
            return [{"ok": 1}]
    """, max_output_chars=4096, wall_clock_s=30)
    assert len(result.stdout) <= 4096 + 200  # cap plus the truncation notice
    assert "truncated" in result.stdout.lower()
    assert any("output" in h.lower() for h in result.limits_hit)


def test_output_written_before_a_timeout_is_still_returned():
    # Otherwise the one case where a user most needs their debug prints — the run
    # that hung — is the case where they get nothing.
    result = run("""
        import time
        def main(database_handler):
            print("got to step 1")
            time.sleep(60)
    """, wall_clock_s=2)
    assert result.timed_out
    assert "got to step 1" in result.stdout


# --- Errors the user has to be able to read ----------------------------------

def test_a_script_exception_comes_back_with_its_own_traceback():
    result = run("""
        def helper():
            raise ValueError("no invoices for that quarter")

        def main(database_handler):
            return helper()
    """)
    assert result.value is None
    assert "no invoices for that quarter" in result.error
    assert "ValueError" in result.traceback
    # The user's frames, named as their file.
    assert "<uploaded script>" in result.traceback
    assert "in helper" in result.traceback


def test_the_traceback_does_not_expose_the_runner_internals():
    result = run("""
        def main(database_handler):
            raise RuntimeError("boom")
    """)
    assert "script_runner_child" not in result.traceback
    assert "/app/" not in result.traceback


def test_a_missing_main_is_reported_even_though_static_checks_exist():
    # The static validator normally catches this first; the runner must not
    # depend on having been called after it.
    result = run("""
        def helper(database_handler):
            return []
    """)
    assert result.error is not None
    assert "main" in result.error


def test_a_syntax_error_is_reported_without_a_crash():
    result = run("def main(database_handler)\n    return []\n")
    assert result.error is not None
    assert result.value is None


def test_a_script_that_exits_early_is_reported_not_silently_empty():
    result = run("""
        import sys
        def main(database_handler):
            sys.exit(3)
    """)
    assert result.error is not None
    assert result.value is None


def test_a_hard_crash_of_the_child_is_reported():
    result = run("""
        import os
        def main(database_handler):
            os._exit(1)
    """)
    assert result.error is not None
    assert result.value is None


def test_output_that_cannot_be_serialized_is_an_error_not_a_hang():
    result = run("""
        def main(database_handler):
            return [{"fn": lambda x: x}]
    """)
    assert result.error is not None


# --- Caps enforced on the parent side, where the script cannot reach them -----

def test_a_query_returning_too_many_rows_raises_into_the_script():
    # Deliberately an exception, not a silent truncation: a script that computes
    # its answer from half the data would produce a plausible, wrong eval set.
    ex = FakeExecutor(rows=[{"n": i} for i in range(50)])
    result = run("""
        def main(database_handler):
            try:
                database_handler.run_sql("SELECT * FROM big")
            except Exception as exc:
                return [{"caught": type(exc).__name__, "msg": str(exc)}]
            return [{"caught": "nothing"}]
    """, ex, max_rows_per_query=10)
    assert result.error is None
    caught = result.value[0]
    assert caught["caught"] != "nothing"
    assert "10" in caught["msg"]
    assert any("row" in h.lower() for h in result.limits_hit)


def test_too_many_queries_raises_into_the_script():
    ex = FakeExecutor()
    result = run("""
        def main(database_handler):
            for i in range(100):
                try:
                    database_handler.run_sql("SELECT 1")
                except Exception as exc:
                    return [{"stopped_at": i, "msg": str(exc)}]
            return [{"stopped_at": -1}]
    """, ex, max_queries=5)
    assert result.error is None
    assert result.value[0]["stopped_at"] == 5
    assert len(ex.calls) == 5
    assert any("quer" in h.lower() for h in result.limits_hit)


def test_a_database_error_surfaces_as_an_exception_the_script_can_catch():
    ex = FakeExecutor(raises=QueryError("permission denied for table payroll"))
    result = run("""
        def main(database_handler):
            try:
                database_handler.run_sql("SELECT * FROM payroll")
            except Exception as exc:
                return [{"msg": str(exc)}]
            return []
    """, ex)
    assert result.error is None
    assert "permission denied" in result.value[0]["msg"]


def test_an_uncaught_database_error_ends_the_run_with_a_readable_message():
    ex = FakeExecutor(raises=QueryError("relation \"nope\" does not exist"))
    result = run("""
        def main(database_handler):
            return database_handler.run_sql("SELECT * FROM nope")
    """, ex)
    assert result.value is None
    assert "does not exist" in result.error


def test_every_query_is_recorded_for_the_audit_log():
    ex = FakeExecutor()
    result = run("""
        def main(database_handler):
            database_handler.run_sql("SELECT 1")
            database_handler.run_sql("SELECT 2", ("x",))
            return []
    """, ex)
    assert [q.sql for q in result.queries] == ["SELECT 1", "SELECT 2"]
    assert [q.rows for q in result.queries] == [1, 1]
    # The audit trail records that parameters were supplied, never their values —
    # they can hold personal data straight out of a business database.
    assert [q.param_count for q in result.queries] == [0, 1]
    assert "x" not in repr(result.queries)


def test_the_database_target_masks_its_password_everywhere_it_is_printed():
    # Lives here rather than beside the other executor tests so that it runs in
    # the default suite: those are skipped without a database, and a password
    # leaking into a log line is not a thing to notice only on a machine that
    # happened to have Postgres running.
    from app.services.script_executor import DbTarget

    target = DbTarget(
        host="db", port=5432, database="sales", user="reader", password="s3cret"
    )
    assert "s3cret" not in repr(target)
    assert "s3cret" not in str(target)
    assert "s3cret" not in f"{target}"
    assert "s3cret" not in repr(target.audit_dict())
    assert target.audit_dict()["user"] == "reader"
