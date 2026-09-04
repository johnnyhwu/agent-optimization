"""Overlaying a user's defaults on the deployment's, and the two ways that goes
wrong.

**Falsiness.** `diagnosis_enabled=False`, `slow_update=False`,
`early_stop_patience=0` and `early_stop_target_score=None` are all answers, not
absences. Every one of them disappears under `if value:` and three of them
disappear under `if value is not None:`. This package has been bitten twice
already — `hyperparams.py` was rewritten because `config.get(k) or default`
turned a deliberate `mixed_weight=0` back into `0.5`, and `stopping._number`
exists because `patience: 0` means "never stop early" and came back as the
environment's number. The overlay therefore keys on **whether the key is
present**, and these tests are what keep it that way.

**`None` is two different things depending on who is asking.** For a *run's*
stored config, `early_stop_target_score: None` means "not set, use the
environment" — that is `stopping._number`'s contract and it is right there.
For a *user's default* it has to be able to mean "off", or a user whose
deployment aims at 0.9 can never say "don't aim at anything by default". So the
overlay must not read user values through `StopPolicy.from_config`; it resolves
the environment first and overlays by key presence second.
"""
from __future__ import annotations

import pytest

from app.auth import normalize_subject
from app.optimizer import hyperparams, stopping
from app.schemas import RunConfig
from app.services import run_config, user_settings


# --- Falsy values are values ------------------------------------------------

def test_a_stored_false_survives_a_true_environment(configure):
    with configure(diagnosis_enabled=True):
        effective = user_settings.run_defaults({"diagnosis_enabled": False})
    assert effective["diagnosis_enabled"] is False


def test_a_stored_zero_survives_a_nonzero_environment(configure):
    with configure(early_stop_patience=5):
        effective = user_settings.optimization_defaults({"early_stop_patience": 0})
    assert effective["early_stop_patience"] == 0


def test_a_stored_zero_share_survives(configure):
    with configure(early_stop_train_error_share=0.25):
        effective = user_settings.optimization_defaults(
            {"early_stop_train_error_share": 0.0}
        )
    assert effective["early_stop_train_error_share"] == 0.0


def test_a_stored_none_target_score_means_off_not_unset(configure):
    """The case `stopping._number` cannot express, and the reason the overlay
    does not go through it."""
    with configure(early_stop_target_score=0.9):
        effective = user_settings.optimization_defaults(
            {"early_stop_target_score": None}
        )
    assert effective["early_stop_target_score"] is None


def test_an_absent_key_keeps_the_environment(configure):
    with configure(early_stop_target_score=0.9, diagnosis_enabled=False):
        effective = user_settings.optimization_defaults({})
        run = user_settings.run_defaults({})
    assert effective["early_stop_target_score"] == 0.9
    assert run["diagnosis_enabled"] is False


def test_a_stored_empty_string_is_an_override_not_an_absence(configure):
    """Clearing the settings page's field removes the key. A key that is present
    and empty is therefore a deliberate "I want this blank", and must not be
    quietly turned back into the environment's value."""
    with configure(agent_chat_url="http://from-env:8080"):
        effective = user_settings.run_defaults({"agent_chat_url": ""})
    assert effective["agent_chat_url"] == ""


# --- The overlay covers exactly the keys it should --------------------------

def test_overlay_never_invents_a_key():
    effective = user_settings.run_defaults({"not_a_setting": "x"})
    assert "not_a_setting" not in effective


def test_run_defaults_has_the_same_shape_as_the_environments():
    assert set(user_settings.run_defaults({})) == set(run_config.defaults())


def test_optimization_defaults_covers_every_key_the_wizard_prefills():
    effective = user_settings.optimization_defaults({})
    for key in run_config.defaults():
        assert key in effective
    for key in hyperparams.algorithm_defaults():
        assert key in effective
    for key in stopping.StopPolicy.from_config({}).as_dict():
        assert key in effective
    for key in ("optimizer_model", "num_epochs", "batch_size"):
        assert key in effective


# --- Reading is forgiving; writing is not -----------------------------------

def test_an_unknown_stored_key_is_dropped_and_reported():
    values, invalid = user_settings.clean({"judge_model": "m", "gone_away": 1})
    assert values == {"judge_model": "m"}
    assert invalid == ["gone_away"]


def test_an_out_of_range_stored_value_is_dropped_rather_than_raised():
    """A value that was legal when it was stored can stop being legal when a
    bound moves. Falling back to the environment costs the user one preference;
    raising here would take out the endpoint that every page loads."""
    values, invalid = user_settings.clean({"concurrency": 9999})
    assert "concurrency" not in values
    assert invalid == ["concurrency"]


def test_a_wrong_typed_value_is_dropped():
    values, invalid = user_settings.clean({"agent_timeout_s": "not a number"})
    assert values == {}
    assert invalid == ["agent_timeout_s"]


def test_clean_coerces_json_round_trip_types():
    """JSONB gives an int back where a float went in. The catalogue's system
    value is the type witness, the same way `hyperparams._coerce` uses it."""
    values, invalid = user_settings.clean({"agent_timeout_s": 90})
    assert invalid == []
    assert isinstance(values["agent_timeout_s"], float)


def test_writing_an_unknown_key_is_an_error():
    with pytest.raises(ValueError):
        user_settings.validate_for_write({"gone_away": 1})


def test_writing_an_out_of_range_value_is_an_error():
    with pytest.raises(ValueError):
        user_settings.validate_for_write({"concurrency": 9999})


def test_writing_a_secret_through_the_values_endpoint_is_an_error():
    """Secrets have their own endpoint so they never share a request body with
    values that are safe to log."""
    with pytest.raises(ValueError):
        user_settings.validate_for_write({"llm_api_key": "sk-live-1234"})


# --- Identity ---------------------------------------------------------------

def test_the_subject_key_is_normalised_the_same_way_shares_are():
    """`eval_set_roles` casefolds; if this did not, `Alice` and `alice` would be
    two people with two sets of defaults and one of them would look empty."""
    assert user_settings.settings_key("Alice ") == normalize_subject("Alice ")
    assert user_settings.settings_key("Alice ") == "alice"
