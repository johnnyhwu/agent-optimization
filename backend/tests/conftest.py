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
