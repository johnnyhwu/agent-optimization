"""Shared test fixtures.

The settings object is a module-level singleton, so tests that need a real seam
configured patch it in place and restore afterwards.
"""
from __future__ import annotations

import contextlib

import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def fast_trace_settle():
    """Take the wall-clock wait out of trace settling (§6.12a), not the reads.

    `settle_trace` re-reads a freshly-arrived trace until its span count stops
    growing, sleeping between reads. Only the sleeping is uninteresting here:
    with the delay at zero every read still happens and every decision is still
    made, so the tests exercise the real thing without paying a second per
    question. A test about the delay itself sets it back via `configure`.
    """
    previous = settings.trace_settle_delay_s
    settings.trace_settle_delay_s = 0.0
    try:
        yield
    finally:
        settings.trace_settle_delay_s = previous


@pytest.fixture
def configure():
    """Temporarily override settings attributes."""

    @contextlib.contextmanager
    def _apply(**overrides):
        previous = {k: getattr(settings, k) for k in overrides}
        for key, value in overrides.items():
            setattr(settings, key, value)
        try:
            yield settings
        finally:
            for key, value in previous.items():
                setattr(settings, key, value)

    return _apply


@pytest.fixture(autouse=True, scope="session")
def sandbox_in_process():
    """Run the sandbox supervisor inside the test process, for the whole suite.

    Not a mock. `app.sandbox.supervisor` is the production code, driven over a
    real socketpair, forking a real child under real rlimits and killing it with
    a real `killpg` — which is what keeps the containment assertions in
    test_script_sandbox.py measuring the thing they claim to measure.

    What it cannot reproduce is the container boundary itself: a different uid,
    an environment with no secrets in it, a `/proc` with nothing to find. Those
    are asserted against a running stack instead — see the `sandbox_container`
    marker.

    Session-scoped and autouse because the tests that need it do not all call
    `run_script` directly: test_script_endpoints.py drives it through the HTTP
    endpoint and test_script_executor.py through a real database round trip, and
    neither has anywhere to pass a transport. One switch reaches all of them.
    """
    previous = settings.script_sandbox_transport
    settings.script_sandbox_transport = "inprocess"
    try:
        yield
    finally:
        settings.script_sandbox_transport = previous
