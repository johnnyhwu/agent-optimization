"""Which seam a run used, and whether the switch that chose it was heard at all.

`OPTIMIZER_IMPL=fake` is the one fake whose output people read directly: the
analyst rationale on the rollout page says "fake analyst: two common patterns
across the minibatch", which reads as a broken model rather than as a switch
that is off. Three things make that answerable, and they are what is tested
here — the run records the seams it executed with, the process says at startup
which ones it got, and it names any setting a `.env` file supplies that the
environment is overruling (the failure that makes a correctly-spelled
`OPTIMIZER_IMPL=real` change nothing).
"""
from __future__ import annotations

import logging
import uuid

import pytest

from app import config
from app.optimizer import runner
from app.optimizer.store import RunSpec


class StubStore:
    """Enough of `OptimizationStore` for `run_optimization_task`'s preamble."""

    def __init__(self, spec: RunSpec | None):
        self.spec = spec
        self.seam_impls: dict[str, str] | None = None

    async def load_run(self, run_id):
        return self.spec

    async def record_seam_impls(self, run_id, impls):
        self.seam_impls = dict(impls)


def _spec(run_id: uuid.UUID) -> RunSpec:
    return RunSpec(
        id=run_id, mode="isolated", skill_name="billing", config={}, secrets={},
        initial_skill={"billing/SKILL.md": "# Billing\n"},
        workspace_baseline=None, detector={},
        num_epochs=1, batch_size=8, steps_per_epoch=1, total_steps=1,
    )


@pytest.fixture
def stubbed(monkeypatch):
    """`run_optimization_task` with its session, seams and loop replaced."""
    run_id = uuid.uuid4()
    store = StubStore(_spec(run_id))

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(runner, "SessionLocal", Session)
    monkeypatch.setattr(runner, "DbOptimizationStore", lambda session: store)
    monkeypatch.setattr(
        runner, "build_seams",
        lambda *a, **k: type("Seams", (), {"optimizer": None})(),
    )

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(runner, "run_optimization", noop)
    return run_id, store


async def test_run_records_the_seams_it_executed_with(stubbed, monkeypatch):
    """Stamped before the first step, from the settings actually in force.

    Recorded at execution rather than at trigger time on purpose: a resumed run
    is executed by a later process, and that restart is exactly when the
    environment changes underneath it.
    """
    run_id, store = stubbed
    monkeypatch.setattr(config.settings, "optimizer_impl", "fake")
    monkeypatch.setattr(config.settings, "agent_impl", "real")

    await runner.run_optimization_task(run_id)

    assert store.seam_impls["optimizer"] == "fake"
    assert store.seam_impls["agent"] == "real"


async def test_no_run_no_stamp(stubbed, monkeypatch):
    """A run id that no longer resolves writes nothing at all."""
    run_id, store = stubbed
    store.spec = None

    await runner.run_optimization_task(run_id)

    assert store.seam_impls is None


def test_seam_impls_covers_every_switch():
    """One entry per `*_IMPL` setting, so the startup line cannot omit a seam."""
    impls = config.seam_impls()
    assert set(impls) == {name for name, _ in config.SEAM_SETTINGS}
    assert "optimizer" in impls


def test_env_file_value_overruled_by_the_environment(tmp_path, monkeypatch):
    """The trap: the file says real, the environment says fake, the file loses.

    This is what a `.env` in the wrong place does under docker compose — compose
    interpolates the repo-root file and passes its own `${OPTIMIZER_IMPL:-fake}`
    default into the container, where it outranks the `backend/.env` the process
    can see. Nothing is wrong with the file, so nothing about it looks wrong.
    """
    (tmp_path / ".env").write_text(
        "# a comment\nOPTIMIZER_IMPL=real\nJUDGE_MODEL=Qwen3.6-27B\nBLANK=\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPTIMIZER_IMPL", "fake")
    monkeypatch.setenv("JUDGE_MODEL", "Qwen3.6-27B")

    overrides = config.env_file_overrides()

    assert overrides == {"OPTIMIZER_IMPL": ("real", "fake")}


def test_no_env_file_is_not_a_finding(tmp_path, monkeypatch):
    """The deployed form has no `.env` in the container; that is normal."""
    monkeypatch.chdir(tmp_path)
    assert config.env_file_overrides() == {}


def test_startup_names_the_seams_and_the_ignored_setting(tmp_path, monkeypatch, caplog):
    """One line for what is in force, one warning per setting being ignored."""
    from app import main

    (tmp_path / ".env").write_text("OPTIMIZER_IMPL=real\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPTIMIZER_IMPL", "fake")
    monkeypatch.setattr(config.settings, "optimizer_impl", "fake")

    with caplog.at_level(logging.INFO, logger="app.main"):
        main.log_seam_configuration()

    info = [r for r in caplog.records if r.levelno == logging.INFO]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("optimizer=fake" in r.getMessage() for r in info)
    assert any("OPTIMIZER_IMPL" in r.getMessage() for r in warnings)
