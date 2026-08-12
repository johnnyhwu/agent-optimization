"""The third-party packages an uploaded script may import, and their limits.

Separate from `test_script_sandbox.py` on purpose: that file is the threat table,
one test per defence, and nothing in it should move because a package was added.
This file covers the other half — that the allow-list actually arrives, that it is
still an allow-list, and that none of the containment changes shape because the
child now has a second directory on its path.

Two tests here need the packages to be installed (they are, in the backend image;
they are not on a bare checkout), so they skip when the directory is not
provisioned. The two that pin the *boundaries* — an unlisted package stays
unavailable, a missing directory stays harmless — run everywhere, because those
are the properties a developer can break without a container in front of them.
"""
from __future__ import annotations

import os
import sys
import textwrap

import pytest

from app.services import script_runner
from app.services.script_runner import Limits, _runner_uid, run_script

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="the sandbox relies on Linux rlimits and process groups",
)

requires_libs = pytest.mark.skipif(
    not os.path.isdir(os.path.join(script_runner.SCRIPT_LIBS, "pandas")),
    reason=(
        f"no script libraries at {script_runner.SCRIPT_LIBS} — run this in the "
        "backend image, or point SCRIPT_LIBS_DIR at an install of "
        "requirements-scripts.txt"
    ),
)


class FakeExecutor:
    """Same stand-in as the sandbox tests use: records what the script asked for."""

    def __init__(self, rows=None):
        self.rows = rows if rows is not None else [{"n": 1}]
        self.calls = []

    def run_sql(self, sql, params):
        self.calls.append((sql, params))
        return list(self.rows)


def run(src, executor=None, **limit_overrides):
    limits = Limits(**limit_overrides)
    return run_script(textwrap.dedent(src), executor or FakeExecutor(), limits)


@requires_libs
def test_pandas_and_tabulate_are_importable_under_the_default_limits():
    """The feature itself — and a guard on `Limits.memory_mb`.

    Deliberately run with the *default* limits rather than generous ones. Import
    of pandas needs roughly 300 MB of address space: it fits inside the 1,024 MB
    default with room to spare, but not inside 256 MB. Anyone lowering that
    default fails here rather than in front of a user whose script stopped
    importing.
    """
    ex = FakeExecutor(rows=[{"name": "ACME", "amount": 3}, {"name": "EMEA", "amount": 4}])
    result = run("""
        import pandas as pd
        from tabulate import tabulate

        def main(database_handler):
            rows = database_handler.run_sql("SELECT name, amount FROM billing")
            frame = pd.DataFrame(rows)
            print(tabulate(frame, headers="keys"))
            total = int(frame["amount"].sum())
            return [{"question": name, "total": total} for name in frame["name"]]
    """, ex)
    assert result.error is None
    assert result.value == [
        {"question": "ACME", "total": 7},
        {"question": "EMEA", "total": 7},
    ]
    # tabulate ran inside the sandbox and its output came back the ordinary way.
    assert "name" in result.stdout and "amount" in result.stdout


@requires_libs
def test_the_library_directory_does_not_widen_the_sandbox(monkeypatch):
    """Every defence still in force in the one run that also imports pandas.

    The failure this catches is not subtle in hindsight and is very easy to
    introduce: making the libraries reachable by relaxing `-I`, or by handing the
    child an environment it can read, would pass the test above and quietly undo
    the uid drop's companions here.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-must-not-leak")
    monkeypatch.setenv("DATABASE_URL", "postgresql://agentopt:hunter2@db/agentopt")
    result = run("""
        import os, resource
        import pandas as pd

        def main(database_handler):
            return [{
                "uid": os.getuid(),
                "env": sorted(os.environ),
                "nproc": resource.getrlimit(resource.RLIMIT_NPROC),
                "fsize": resource.getrlimit(resource.RLIMIT_FSIZE),
                "pandas": pd.__version__,
            }]
    """, max_processes=32)
    assert result.error is None
    seen = result.value[0]
    assert seen["nproc"] == [32, 32]
    assert seen["fsize"] == [0, 0]
    assert "OPENAI_API_KEY" not in seen["env"]
    assert "DATABASE_URL" not in seen["env"]
    assert "hunter2" not in repr(result.value)
    if _runner_uid() is not None:
        assert seen["uid"] != os.getuid()
        assert seen["uid"] != 0


@requires_libs
def test_a_library_allocation_still_hits_the_memory_limit():
    """numpy allocates in C, where RLIMIT_AS is the only thing in the way.

    A Python-level memory hog is already covered next door; this is the same
    limit approached from the other side, because the packages added here are the
    reason a script can now ask for a gigabyte in a single call.
    """
    result = run("""
        import numpy as np

        def main(database_handler):
            return [{"shape": np.zeros((1024, 1024, 1024), dtype="float64").shape}]
    """, memory_mb=512, wall_clock_s=30)
    assert result.value is None
    assert result.error  # a reported failure, not a hang and not a crash of ours
    assert not result.timed_out
    assert result.orphans == 0


def test_a_package_that_is_not_on_the_list_is_unavailable():
    """An allow-list, not "third-party packages are supported now".

    scipy is the stand-in for everything nobody decided to ship: it is not in
    requirements-scripts.txt and not in the server's own dependencies either, so
    a run that imports it must fail — readably, with the name of the module, in
    the same shape as any other script error.
    """
    result = run("""
        import scipy

        def main(database_handler):
            return [{"question": "unreachable"}]
    """)
    assert result.value is None
    assert "ModuleNotFoundError" in (result.error or "")
    assert "scipy" in (result.error or "")


def test_a_missing_library_directory_does_not_break_a_run(monkeypatch, tmp_path):
    """A checkout with no libraries installed behaves exactly as it did before.

    The path is a build artefact of the image, so every environment that is not
    that image — a developer's machine, a test runner outside Docker — has
    nothing at it. Skipping the entry has to be silent, or the feature would be
    broken everywhere it is not deployed.
    """
    monkeypatch.setattr(script_runner, "SCRIPT_LIBS", str(tmp_path / "not-provisioned"))
    result = run("""
        import json

        def main(database_handler):
            rows = database_handler.run_sql("SELECT 1")
            return [{"question": json.dumps(rows)}]
    """)
    assert result.error is None
    assert result.value == [{"question": '[{"n": 1}]'}]
