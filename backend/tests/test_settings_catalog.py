"""The catalogue is the only list of what a user may set, and the only thing
standing between this feature and a slow drift into uselessness.

A user default exists for a key when two things are true: the deployment
configures it through an environment variable, **and** some form in the browser
already lets a developer override it for one run. Neither half is visible from
the other. `Settings` grows a field; six weeks later somebody puts a control on
the Optimize wizard for it; nobody connects the two, and the settings page
quietly stops covering the thing people actually retype every day. Nothing
fails, nothing warns, and the omission is only findable by reading two files
side by side.

So the two halves are checked from both directions, and a third check catches
the reverse mistake:

  A. every field on `Settings` is either offered by the catalogue or listed in
     `EXCLUDED_SETTINGS` **with a written reason**
  B. every key in the three default dictionaries the /defaults endpoints are
     built from is offered or listed in `EXCLUDED_KEYS`, same rule
  C. every catalogue entry has a variable in `.env.example` — which is what
     stops a field being added to the settings page that no deployment can
     configure, i.e. one that fails the first half of the condition

The reason strings are mandatory and that is the point. "No control on any form
yet" and "containment boundary, not a preference" are different decisions with
different futures, and a bare set of excluded names records neither. The
frontend half of the same contract lives in
`frontend/src/settings_catalog.test.js`.
"""
from __future__ import annotations

from pathlib import Path

from app import settings_catalog as catalog
from app.config import Settings
from app.optimizer import hyperparams, stopping
from app.schemas import OptimizationConfig, OptimizationRunCreate, RunConfig
from app.services import run_config

REPO_ROOT = Path(__file__).resolve().parents[2]


# --- A: nothing on Settings escapes a decision ------------------------------

def test_every_settings_field_is_either_offered_or_excluded_with_a_reason():
    offered = {spec.setting for spec in catalog.CATALOG}
    undecided = [
        name
        for name in Settings.model_fields
        if name not in offered and name not in catalog.EXCLUDED_SETTINGS
    ]
    assert not undecided, (
        "These environment settings are neither offered on the settings page nor "
        "excluded from it: " + ", ".join(sorted(undecided)) + ". Add each to "
        "settings_catalog.CATALOG (it has a control on some form) or to "
        "EXCLUDED_SETTINGS with the reason it does not belong there."
    )


def test_no_setting_is_both_offered_and_excluded():
    offered = {spec.setting for spec in catalog.CATALOG}
    both = offered & set(catalog.EXCLUDED_SETTINGS)
    assert not both, f"offered and excluded at the same time: {sorted(both)}"


def test_every_exclusion_carries_a_reason():
    for mapping, label in (
        (catalog.EXCLUDED_SETTINGS, "EXCLUDED_SETTINGS"),
        (catalog.EXCLUDED_KEYS, "EXCLUDED_KEYS"),
    ):
        blank = [k for k, why in mapping.items() if not (why or "").strip()]
        assert not blank, (
            f"{label} entries with no reason: {sorted(blank)}. The reason is the "
            "record of a decision; without it the next reader cannot tell a "
            "deliberate omission from an oversight."
        )


def test_excluded_names_still_exist():
    """An exclusion for a setting that has been deleted is stale bookkeeping."""
    unknown = [n for n in catalog.EXCLUDED_SETTINGS if n not in Settings.model_fields]
    assert not unknown, (
        f"EXCLUDED_SETTINGS names settings that no longer exist: {sorted(unknown)}"
    )


# --- B: nothing in the /defaults payloads escapes a decision ----------------

def _default_keys() -> set[str]:
    """Every key the two /defaults endpoints are assembled from.

    The same three functions the endpoints call, so a key added to any of them
    is a key this test sees on the next run.
    """
    return (
        set(run_config.defaults())
        | set(hyperparams.algorithm_defaults())
        | set(stopping.StopPolicy.from_config({}).as_dict())
        | {"optimizer_model", "num_epochs", "batch_size"}
    )


def test_every_default_key_is_either_offered_or_excluded_with_a_reason():
    offered = {spec.key for spec in catalog.CATALOG}
    undecided = [
        key
        for key in _default_keys()
        if key not in offered and key not in catalog.EXCLUDED_KEYS
    ]
    assert not undecided, (
        "These prefilled run settings are neither offered on the settings page nor "
        "excluded from it: " + ", ".join(sorted(undecided)) + ". Add each to "
        "settings_catalog.CATALOG or to EXCLUDED_KEYS with a reason."
    )


def test_catalogue_keys_are_unique():
    keys = [spec.key for spec in catalog.CATALOG]
    assert len(keys) == len(set(keys)), "duplicate key in CATALOG"
    settings_attrs = [spec.setting for spec in catalog.CATALOG]
    assert len(settings_attrs) == len(set(settings_attrs)), "duplicate setting in CATALOG"


def test_every_catalogue_entry_names_a_real_setting():
    unknown = [s.setting for s in catalog.CATALOG if s.setting not in Settings.model_fields]
    assert not unknown, f"CATALOG names settings that do not exist: {sorted(unknown)}"


# --- C: every offered key is actually configurable by a deployment ----------

def test_every_catalogue_entry_has_a_variable_in_env_example():
    text = (REPO_ROOT / ".env.example").read_text()
    missing = [
        spec.setting.upper()
        for spec in catalog.CATALOG
        if spec.setting.upper() not in text
    ]
    assert not missing, (
        "These settings are offered on the settings page but have no variable in "
        ".env.example: " + ", ".join(sorted(missing)) + ". A user default is only "
        "offered for a key a deployment can configure; document the variable, or "
        "drop the entry."
    )


# --- Bounds: the catalogue may not be looser than what accepts the value ----

def _pydantic_bounds(model, field: str) -> tuple[float | None, float | None]:
    """(minimum, maximum) as the request schema enforces them."""
    info = model.model_fields.get(field)
    if info is None:
        return (None, None)
    low = high = None
    for meta in info.metadata:
        low = getattr(meta, "ge", None) if getattr(meta, "ge", None) is not None else low
        high = getattr(meta, "le", None) if getattr(meta, "le", None) is not None else high
    return (low, high)


def test_catalogue_bounds_are_no_looser_than_any_consumer():
    """A stored default outside a form's range is an error on a value nobody typed.

    `concurrency` is the live example: `RunConfig` accepts any integer above
    zero, and the Optimize wizard caps it at 32. A user default of 64 is
    accepted here, prefilled there, and then rejected by a form the user has not
    touched — so the catalogue takes the *strictest* bound of everything that
    consumes the key, not the loosest.
    """
    consumers = (RunConfig, OptimizationConfig, OptimizationRunCreate)
    for spec in catalog.CATALOG:
        for model in consumers:
            low, high = _pydantic_bounds(model, spec.key)
            if low is not None:
                assert spec.minimum is not None and spec.minimum >= low, (
                    f"{spec.key}: catalogue minimum {spec.minimum} is below "
                    f"{model.__name__}'s {low}"
                )
            if high is not None:
                assert spec.maximum is not None and spec.maximum <= high, (
                    f"{spec.key}: catalogue maximum {spec.maximum} is above "
                    f"{model.__name__}'s {high}"
                )


# --- The JSON the frontend contract test reads ------------------------------

def test_the_exported_json_matches_the_catalogue():
    """`frontend/src/settings_catalog.json` is generated, checked in, and read by
    a `node --test` file that cannot import Python. This is the half that keeps
    it honest; the other half is the frontend test that reads it.
    """
    import json

    exported = json.loads(
        (REPO_ROOT / "frontend" / "src" / "settings_catalog.json").read_text()
    )
    assert exported == catalog.as_json(), (
        "frontend/src/settings_catalog.json is out of date. Regenerate it with "
        "`python -m app.settings_catalog > ../frontend/src/settings_catalog.json`."
    )
