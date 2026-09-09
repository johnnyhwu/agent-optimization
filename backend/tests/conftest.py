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


@pytest.fixture(scope="session")
def repo_root():
    """The repository root, for the tests that assert a contract with a file
    outside `backend/`.

    A handful of them have to: the settings catalogue is answerable only against
    the root `.env.example` and the JSON the frontend reads, the session-expiry
    marker is one string in two languages, and the packaging guards read
    `.dockerignore` and `docker-compose.override.yml`. None of those files is in
    the backend image, and none should be — the image's build context is
    `./backend`.

    So the path cannot be derived from `__file__` alone. In a checkout it is two
    levels up; in the image that is `/`, where nothing matches and the tests
    would fail on files that were never meant to be there. `SKILL_STUDIO_REPO_ROOT`
    is how CI closes that gap: backend/azure-pipelines.yml bind-mounts the
    checkout into the test container and points this at it, so these run in the
    pipeline rather than skipping. The skip is the last resort, for an image run
    with no checkout beside it — and it is deliberately conditioned on the tree
    being absent, not on one file being missing, so a *deleted* contract file
    still fails instead of quietly skipping.
    """
    import os
    from pathlib import Path

    override = os.environ.get("SKILL_STUDIO_REPO_ROOT")
    root = Path(override) if override else Path(__file__).resolve().parents[2]
    if not (root / "backend").is_dir() or not (root / "frontend").is_dir():
        pytest.skip(
            f"no repository checkout at {root} — these assert contracts with "
            "files that live outside the backend image (set SKILL_STUDIO_REPO_ROOT)"
        )
    return root
