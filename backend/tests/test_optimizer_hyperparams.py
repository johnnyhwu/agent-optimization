"""The algorithm's knobs: one set of defaults, and the falsy values that survive.

Two things are being protected here, and neither of them fails loudly.

The first is that a run's stored config is a *complete* record of how it was
run. Seven of these settings have no control in the wizard, so nothing sent
them and nothing stored them, and the value that actually ran was a literal in
the engine. Change that literal and every finished run is retroactively
described wrong — with no error, no migration, and nothing on the page to say
the number moved.

The second is `or`. Every one of these used to be read as
`config.get(key) or <default>`, which cannot tell "not set" from a falsy value
someone chose. `mixed_weight: 0` is legal (`ge=0` in the schema) and means
"compare on hard accuracy alone"; it came back out as 0.5, which is a different
gate on every step of the run.
"""
from __future__ import annotations

from app.config import settings
from app.optimizer.hyperparams import algorithm_defaults, resolve_algorithm


def test_an_empty_config_resolves_to_the_environment():
    resolved = resolve_algorithm({})

    assert resolved == algorithm_defaults()
    assert resolved["minibatch_size"] == settings.optimizer_minibatch_size
    assert resolved["gate_metric"] == settings.optimizer_gate_metric


def test_every_key_is_present_whatever_the_run_sent():
    """The point of the record: no key is ever missing, so nothing falls back.

    A key absent here is a key the engine would have to carry a literal for,
    and a literal in the engine is the second copy this module exists to
    remove.
    """
    resolved = resolve_algorithm({"minibatch_size": 4})

    assert set(resolved) == set(algorithm_defaults())


def test_a_runs_own_value_wins():
    resolved = resolve_algorithm({"minibatch_size": 4, "scheduler": "linear"})

    assert resolved["minibatch_size"] == 4
    assert resolved["scheduler"] == "linear"


def test_a_deliberate_zero_is_not_an_unset_field():
    """`mixed_weight: 0` is "hard accuracy alone", not "use the default".

    Under the `or` idiom this came back as 0.5, so a caller asking the gate to
    ignore soft scores got a gate that weighted them at half — on every step,
    with the chart showing nothing unusual.
    """
    assert resolve_algorithm({"mixed_weight": 0})["mixed_weight"] == 0.0


def test_a_deliberate_false_is_not_an_unset_field(monkeypatch):
    monkeypatch.setattr(settings, "optimizer_failure_only", True)

    assert resolve_algorithm({"failure_only": False})["failure_only"] is False


def test_a_blank_string_falls_back_rather_than_selecting_nothing():
    """Blank is how this API spells "the deployment did not say".

    A scheduler of "" would reach `build_scheduler` as an unknown mode.
    """
    assert resolve_algorithm({"scheduler": ""})["scheduler"] == settings.optimizer_scheduler


def test_numbers_come_back_as_numbers_whatever_json_did_to_them():
    """JSONB round-trips are loose and the vendored stages type-check nothing."""
    resolved = resolve_algorithm({"minibatch_size": "6", "mixed_weight": "0.25"})

    assert resolved["minibatch_size"] == 6 and isinstance(resolved["minibatch_size"], int)
    assert resolved["mixed_weight"] == 0.25 and isinstance(resolved["mixed_weight"], float)


def test_the_defaults_follow_the_environment(monkeypatch):
    """The wizard's prefill and the value the loop uses are one setting.

    They were two literals in two files, which is a difference that only shows
    up as a run behaving unlike the form that started it.
    """
    monkeypatch.setattr(settings, "optimizer_minibatch_size", 3)

    assert algorithm_defaults()["minibatch_size"] == 3
    assert resolve_algorithm({})["minibatch_size"] == 3
