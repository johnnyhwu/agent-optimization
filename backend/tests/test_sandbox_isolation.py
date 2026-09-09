"""The assertions that only mean something with a real container boundary.

Everything in test_script_sandbox.py runs the supervisor inside the pytest
process, which is the right trade for a unit suite — it is the real supervisor,
forking a real child under real rlimits — but it cannot check the one thing the
sidecar split was actually for. In that process the supervisor *is* us, so
"the script cannot read our secrets" has nothing to compare against.

These run against a started stack instead:

    make up            # or docker compose up -d
    make test-sandbox

Marked `sandbox_container` and deselected by default (see pytest.ini), because a
test that silently skips is a test that has stopped being read. When these are
not run, nothing has verified the boundary — only that the machinery around it
still works.
"""
from __future__ import annotations

import os
import textwrap

import pytest

from app.sandbox.model import Limits
from app.services.script_runner import run_script

pytestmark = pytest.mark.sandbox_container


class FakeExecutor:
    def run_sql(self, sql, params):
        return [{"n": 1}]


def run(src, **overrides):
    """Run against the real sandbox container, whatever the suite's default is."""
    from app.sandbox import transport

    def connect(limits):
        return transport.sidecar(limits)

    return run_script(textwrap.dedent(src), FakeExecutor(), Limits(**overrides), connect=connect)


def test_the_script_runs_as_a_different_unprivileged_uid():
    """The boundary, stated as plainly as it can be stated."""
    result = run("""
        import os
        def main(database_handler):
            return [{"question": str(os.getuid())}]
    """)
    assert result.error is None
    uid = int(result.value[0]["question"])
    assert uid != 0, "the sandbox must not run uploaded code as root"
    assert uid != os.getuid(), "the script must not run as the backend"


def test_the_backends_secrets_are_not_reachable_through_proc():
    """The attack this whole change exists to close.

    `/proc/1/environ` is the environment of the container's first process. In the
    old arrangement — and in any arrangement where the script shares the
    backend's uid — that is the backend's own environment, and it carries the
    database password, the Fernet key every user's stored credential is encrypted
    with, and the API keys. Here PID 1 is the supervisor, in a container that was
    given none of them.

    Note the check is on the *values* being absent, not on the read failing.
    Whether the file opens is an implementation detail of the platform; whether
    a credential comes back is the property.
    """
    result = run("""
        def main(database_handler):
            try:
                with open("/proc/1/environ", "rb") as fh:
                    blob = fh.read().decode("utf-8", "replace")
            except OSError as exc:
                blob = f"unreadable: {exc}"
            return [{"question": blob}]
    """)
    assert result.error is None
    seen = result.value[0]["question"]
    for name in (
        "DATABASE_URL",
        "SYNC_DATABASE_URL",
        "SETTINGS_SECRET_KEY",
        "LLM_API_KEY",
        "LANGFUSE_SECRET_KEY",
        "AGENT_API_KEY",
        "POSTGRES_PASSWORD",
    ):
        assert name not in seen, f"{name} is reachable from an uploaded script"


def test_pandas_cannot_be_used_to_read_the_environment_either():
    """The bypass that makes an import blacklist worthless, tried directly.

    A static check that forbids `import os` and `open()` looks like containment
    until you remember the sandbox hands the script pandas, and pandas is a file
    reader. This is here so that the reason the project has no import blacklist
    (`script_validate.py`'s docstring) stays a demonstrated fact rather than an
    assertion in a comment.
    """
    result = run("""
        import pandas as pd
        def main(database_handler):
            try:
                frame = pd.read_csv("/proc/1/environ", sep="\\0", header=None,
                                    engine="python")
                text = frame.to_string()
            except Exception as exc:
                text = f"unreadable: {exc}"
            return [{"question": text}]
    """)
    assert result.error is None
    seen = result.value[0]["question"]
    assert "SETTINGS_SECRET_KEY" not in seen
    assert "DATABASE_URL" not in seen


def test_the_ca_bundle_is_not_mounted_in_the_sandbox():
    """`/app/certs` is the backend's, and the sandbox has no business with TLS."""
    result = run("""
        import os
        def main(database_handler):
            try:
                entries = sorted(os.listdir("/app/certs"))
            except OSError as exc:
                entries = [f"unreadable: {exc}"]
            return [{"question": repr(entries)}]
    """)
    assert result.error is None
    seen = result.value[0]["question"]
    assert ".crt" not in seen and ".pem" not in seen


def test_a_killed_script_leaves_no_orphans_where_the_supervisor_is_pid_1():
    """The regression `_reap_group` exists for, and the only place it shows.

    In the sandbox container the supervisor is PID 1, so a grandchild orphaned by
    `killpg` is reparented onto *it* — and a zombie still answers signal 0, which
    is what `_survivors` polls. Without an explicit reap, every timed-out run
    would report an orphan that is not there. No in-process test can catch this,
    because the pytest process is not PID 1.
    """
    result = run("""
        import subprocess, sys, time
        def main(database_handler):
            subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            time.sleep(30)
            return []
    """, wall_clock_s=3)
    assert result.timed_out is True
    assert result.orphans == 0
