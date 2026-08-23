"""User defaults prefill forms. They do not change what a run executes.

This is the one rule in the feature that is invisible from its call sites, so it
gets a test of its own rather than a comment.

`run_config.defaults()` looks like the obvious place to overlay a user's values —
it is the function the "Run eval" dialog is prefilled from. But `resolve()` calls
it too, and `resolve()` is what decides the settings a run actually executes
with. The same trap is set twice more: `hyperparams.resolve_algorithm()` calls
`algorithm_defaults()`, and the optimizer engine calls
`StopPolicy.from_config()` once per step. Teaching any of those three about the
caller would mean the same POST producing different runs for different people,
decided in a file nobody would think to open — and it would happen silently,
because every existing test would still pass.

So the overlay lives in `services/user_settings.py`, which the two /defaults
endpoints call and nothing else does. These tests pin that from three angles:
the three functions take no identity, their modules do not know the overlay
exists, and a blank request still resolves to the environment.
"""
from __future__ import annotations

import inspect
from pathlib import Path

from app.optimizer import hyperparams, stopping
from app.schemas import RunConfig
from app.services import run_config

BACKEND = Path(__file__).resolve().parents[1]

# The three functions that must stay unaware of who is asking, and the module
# each lives in.
RESOLUTION_PATH = (
    (run_config.defaults, "app/services/run_config.py"),
    (hyperparams.algorithm_defaults, "app/optimizer/hyperparams.py"),
    (stopping.StopPolicy.from_config, "app/optimizer/stopping.py"),
)


def test_the_resolution_path_takes_no_identity():
    for func, where in RESOLUTION_PATH:
        params = set(inspect.signature(func).parameters)
        leaked = params & {"subject", "session", "user", "user_settings"}
        assert not leaked, (
            f"{where}: {func.__name__} has grown {sorted(leaked)}. This function is "
            "on the path a run actually executes through; giving it the caller's "
            "identity makes the same request produce different runs for different "
            "people. Overlay in services/user_settings.py instead."
        )


def test_the_resolution_path_does_not_import_the_overlay():
    for _, where in RESOLUTION_PATH:
        source = (BACKEND / where).read_text()
        assert "user_settings" not in source, (
            f"{where} references user_settings. See this module's docstring: the "
            "overlay belongs to the /defaults endpoints, not to the path a run "
            "executes through."
        )


def test_a_blank_run_config_still_resolves_to_the_environment():
    resolved = run_config.resolve(RunConfig())
    for key, value in run_config.defaults().items():
        assert resolved[key] == value


def test_a_blank_algorithm_config_still_resolves_to_the_environment():
    assert hyperparams.resolve_algorithm({}) == hyperparams.algorithm_defaults()


def test_a_blank_stop_policy_still_resolves_to_the_environment(configure):
    with configure(early_stop_patience=7, early_stop_target_score=0.8):
        policy = stopping.StopPolicy.from_config({})
    assert policy.patience == 7
    assert policy.target_score == 0.8
