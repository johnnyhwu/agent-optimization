"""Shared test fixtures.

The settings object is a module-level singleton, so tests that need a real seam
configured patch it in place and restore afterwards.
"""
from __future__ import annotations

import contextlib

import pytest

from app.config import settings


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
