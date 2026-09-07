"""A stored credential is a longer-lived thing than a run's, and is protected
accordingly.

`runs.secrets` is plaintext in a JSONB column, and that is defensible: it is one
afternoon's key, written by the person who typed it, readable only by a process
that already holds the database password. A *user default* is the same key with
no expiry, sitting there across every run anyone starts. Same value, an order of
magnitude more blast radius — so it is encrypted at rest, it is never returned,
and it is only ever injected server-side.

Four rules carry the weight, and each fails in a way nothing else would catch:

  * **Fail closed.** No `SETTINGS_SECRET_KEY`, no feature. Never a plaintext
    fallback, because that failure is silent and permanent.
  * **Endpoint binding.** A credential is only injected when the endpoint it
    authenticates against is the one it was stored for. Without it, editing the
    LLM base URL in the run dialog would send the user's key to whatever address
    they typed — `routers/runs.py::_resolve_secrets` already carries this rule
    for borrowed run credentials and its docstring says why.
  * **Not in fake auth.** `AUTH_MODE=fake` takes the caller's word for who they
    are, from a header. Long-lived credentials keyed by that are a hole, so the
    store is refused at both ends rather than hidden in the UI.
  * **Decryption failure is not an outage.** A rotated or lost key must degrade
    to "there is no stored credential"; raising would take down the endpoint
    every page loads.
"""
from __future__ import annotations

import pytest

from app import settings_catalog as catalog
from app.services import user_secrets

KEY = "kBv6H0kQ2p6b0iC5j3RiUcDnJ5c1RzOaTQnUvGZlp1U="
OTHER_KEY = "0S9CoUzYQ9VsxCFHkbQnB3aMTNMzTVjPUYc5UgSXwl4="


# --- Fail closed ------------------------------------------------------------

def test_the_feature_is_off_when_no_encryption_key_is_configured(configure):
    with configure(settings_secret_key="", auth_mode="keycloak"):
        assert user_secrets.available() is False


def test_storing_without_an_encryption_key_is_refused_rather_than_stored_plain(configure):
    with configure(settings_secret_key="", auth_mode="keycloak"):
        with pytest.raises(user_secrets.SecretsUnavailable):
            user_secrets.entry("llm_api_key", "sk-live-1234", endpoint="http://llm")


# --- Not in fake auth -------------------------------------------------------

def test_fake_auth_disables_the_store(configure):
    with configure(settings_secret_key=KEY, auth_mode="fake"):
        assert user_secrets.available() is False


def test_fake_auth_refuses_a_write(configure):
    with configure(settings_secret_key=KEY, auth_mode="fake"):
        with pytest.raises(user_secrets.SecretsUnavailable):
            user_secrets.entry("llm_api_key", "sk-live-1234", endpoint="http://llm")


def test_fake_auth_injects_nothing_even_if_rows_exist(configure):
    """Belt and braces: a deployment switched from keycloak to fake still has the
    rows, and must stop using them."""
    with configure(settings_secret_key=KEY, auth_mode="keycloak"):
        stored = {"llm_api_key": user_secrets.entry(
            "llm_api_key", "sk-live-1234", endpoint="http://llm"
        )}
    with configure(settings_secret_key=KEY, auth_mode="fake"):
        injected = user_secrets.inject(stored, {"llm_base_url": "http://llm"}, {})
    assert injected == {}


# --- Round trip -------------------------------------------------------------

def test_a_stored_credential_is_not_readable_from_the_row(configure):
    with configure(settings_secret_key=KEY, auth_mode="keycloak"):
        entry = user_secrets.entry("llm_api_key", "sk-live-1234", endpoint="http://llm")
    assert "sk-live-1234" not in str(entry)
    assert entry["endpoint"] == "http://llm"


def test_a_stored_credential_is_injected_for_its_own_endpoint(configure):
    with configure(settings_secret_key=KEY, auth_mode="keycloak"):
        stored = {"llm_api_key": user_secrets.entry(
            "llm_api_key", "sk-live-1234", endpoint="http://llm"
        )}
        injected = user_secrets.inject(stored, {"llm_base_url": "http://llm"}, {})
    assert injected == {"llm_api_key": "sk-live-1234"}


def test_the_public_view_says_whether_a_credential_is_set_and_nothing_else(configure):
    with configure(settings_secret_key=KEY, auth_mode="keycloak"):
        stored = {"llm_api_key": user_secrets.entry(
            "llm_api_key", "sk-live-1234", endpoint="http://llm"
        )}
        view = user_secrets.public_view(stored)
    assert view["llm_api_key"]["set"] is True
    assert "sk-live-1234" not in str(view)
    assert "ciphertext" not in str(view)
    # Not even the last four characters: a fingerprint is still a fingerprint.
    assert "1234" not in str(view)


# --- Endpoint binding -------------------------------------------------------

def test_a_credential_is_not_injected_for_a_different_endpoint(configure):
    with configure(settings_secret_key=KEY, auth_mode="keycloak"):
        stored = {"llm_api_key": user_secrets.entry(
            "llm_api_key", "sk-live-1234", endpoint="http://llm"
        )}
        injected = user_secrets.inject(
            stored, {"llm_base_url": "http://attacker.example"}, {}
        )
    assert injected == {}


def test_langfuse_binds_to_its_own_host(configure):
    with configure(settings_secret_key=KEY, auth_mode="keycloak"):
        stored = {"langfuse_secret_key": user_secrets.entry(
            "langfuse_secret_key", "lf-secret", endpoint="http://lf"
        )}
        matched = user_secrets.inject(stored, {"langfuse_host": "http://lf"}, {})
        mismatched = user_secrets.inject(stored, {"langfuse_host": "http://other"}, {})
    assert matched == {"langfuse_secret_key": "lf-secret"}
    assert mismatched == {}


def test_an_explicitly_typed_credential_wins_over_the_stored_one(configure):
    with configure(settings_secret_key=KEY, auth_mode="keycloak"):
        stored = {"llm_api_key": user_secrets.entry(
            "llm_api_key", "sk-stored", endpoint="http://llm"
        )}
        injected = user_secrets.inject(
            stored, {"llm_base_url": "http://llm"}, {"llm_api_key": "sk-typed"}
        )
    assert injected == {"llm_api_key": "sk-typed"}


# --- Degradation ------------------------------------------------------------

def test_an_undecryptable_credential_is_treated_as_absent(configure):
    with configure(settings_secret_key=KEY, auth_mode="keycloak"):
        stored = {"llm_api_key": user_secrets.entry(
            "llm_api_key", "sk-live-1234", endpoint="http://llm"
        )}
    with configure(settings_secret_key=OTHER_KEY, auth_mode="keycloak"):
        injected = user_secrets.inject(stored, {"llm_base_url": "http://llm"}, {})
        view = user_secrets.public_view(stored)
    assert injected == {}
    assert view["llm_api_key"]["readable"] is False


def test_a_corrupt_entry_does_not_raise(configure):
    with configure(settings_secret_key=KEY, auth_mode="keycloak"):
        assert user_secrets.inject({"llm_api_key": {"nonsense": 1}}, {}, {}) == {}


# --- The mapping has one home -----------------------------------------------

def test_the_endpoint_mapping_comes_from_the_catalogue():
    """`routers/runs.py` had its own copy for borrowed run credentials. Two
    copies of "which URL does this key authenticate against" is one copy too
    many, so the catalogue owns it and runs.py reads it from there."""
    from app.routers.runs import _SECRET_ENDPOINTS

    assert _SECRET_ENDPOINTS == {
        spec.key: spec.endpoint_key
        for spec in catalog.CATALOG
        if spec.kind == "secret"
    }
    assert _SECRET_ENDPOINTS == {
        "llm_api_key": "llm_base_url",
        "langfuse_secret_key": "langfuse_host",
        # Bound to the chat endpoint, and reaching the skills endpoint only when
        # that is the same server — see integrations/real/agent_auth.py.
        "agent_api_key": "agent_chat_url",
    }
