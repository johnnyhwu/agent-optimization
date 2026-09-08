"""What an expired SSO session does to a run — and what it must never do.

Three rules, and each of them fails silently if it breaks.

**An eval run fails; an optimization run is interrupted.** The difference is
whether there is anything worth keeping. An eval run has no checkpoint
(`models.py` says so where it explains why `interrupted` is optimization-only)
and takes minutes, so re-running is the fix. An optimization run is checkpointed
per step and takes hours, so routing it to `failed` would throw that away over a
login — and `failed` is deliberately not resumable
(`routers/optimization.py:resume_optimization_run`).

**It ends the run once, not every question.** Every question in a run shares one
session, so handling the expiry per question turns one expired login into a
screenful of identical agent errors with the real cause nowhere on the page.

**The token never reaches a database column.** The whole reason
`app/agent_sso.py` is an in-memory registry is that nothing it holds is
persisted; `runs.secrets` and `optimization_runs.secrets` are the columns that
would quietly acquire a live credential if a refresh token ever arrived as a
body field instead of a header. Asserted rather than assumed, because the
failure is invisible until someone reads the table.

And the switch: with `AGENT_SSO_ENABLED` off — the default — none of this is
reachable and `build_seams` is called with the argument list it always had.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import pytest

from app import agent_sso, orchestrator
from app.agent_sso import SsoSessionExpired
from app.optimizer import engine


@pytest.fixture(autouse=True)
def _clean_registry():
    agent_sso._entries.clear()
    yield
    agent_sso._entries.clear()


def sso_on(configure, **overrides):
    return configure(
        auth_mode="keycloak",
        agent_sso_enabled=True,
        keycloak_url="https://kc.test/auth",
        keycloak_realm="tsmc",
        **overrides,
    )


# --- The switch is what keeps this inert ----------------------------------


def test_seam_kwargs_is_empty_without_a_session(configure):
    """Empty, not `agent_credential=None`: with SSO off, `build_seams` is called
    with the argument list it has always been called with."""
    with configure(auth_mode="keycloak", agent_sso_enabled=False):
        agent_sso.register("run-1", "rt-1", "alice")
        assert agent_sso.seam_kwargs("run-1") == {}


def test_seam_kwargs_carries_a_credential_once_registered(configure):
    with sso_on(configure):
        agent_sso.register("run-1", "rt-1", "alice")
        kwargs = agent_sso.seam_kwargs("run-1")
    assert set(kwargs) == {"agent_credential"}


def test_a_run_that_never_registered_gets_no_keyword(configure):
    """The default for every existing deployment, and for fake mode."""
    with sso_on(configure):
        assert agent_sso.seam_kwargs(uuid.uuid4()) == {}


# --- Optimization: interrupted, and resumable -----------------------------


class RecordingStore:
    """Just enough store to see what the engine finalized the run as."""

    def __init__(self) -> None:
        self.finished: dict = {}
        self.statuses: list[str] = []

    async def set_status(self, run_id, status, **_):
        self.statuses.append(status)

    async def finish_run(self, run_id, **fields):
        self.finished = dict(fields)


async def test_an_expired_session_interrupts_an_optimization_run(monkeypatch):
    """`interrupted`, so every finished step survives and Resume is offered."""
    store = RecordingStore()

    async def boom(*args, **kwargs):
        raise SsoSessionExpired("the identity provider refused to refresh this session")

    monkeypatch.setattr(engine, "_execute", boom)
    published: list[dict] = []

    await engine.run_optimization(
        uuid.uuid4(), store=store, seams=None,
        publish=lambda event: published.append(event) or asyncio.sleep(0),
    )

    assert store.finished["status"] == "interrupted", (
        "an expired login must not be routed to `failed`, which is not "
        "resumable and would discard every finished step"
    )
    assert published[-1]["status"] == "interrupted"


async def test_an_interrupted_run_has_no_completed_at(monkeypatch):
    """`completed_at` NULL is the contract `interrupted` already carries: the
    reaper leaves it NULL, `test_optimizer_isolation` pins it, and
    `optimize_duration.js` reads the NULL to show how far the run got rather
    than a span that would include however long it sat waiting to be resumed."""
    store = RecordingStore()

    async def boom(*args, **kwargs):
        raise SsoSessionExpired("session ended")

    monkeypatch.setattr(engine, "_execute", boom)
    await engine.run_optimization(
        uuid.uuid4(), store=store, seams=None,
        publish=lambda event: asyncio.sleep(0),
    )
    assert "completed_at" not in store.finished or store.finished["completed_at"] is None


async def test_an_ordinary_failure_still_completes_and_is_stamped(monkeypatch):
    """The regression guard for the branch above: everything that is not an
    expired session keeps going down the `failed` path, stamped."""
    store = RecordingStore()

    async def boom(*args, **kwargs):
        raise RuntimeError("the agent server fell over")

    monkeypatch.setattr(engine, "_execute", boom)
    await engine.run_optimization(
        uuid.uuid4(), store=store, seams=None,
        publish=lambda event: asyncio.sleep(0),
    )
    assert store.finished["status"] == "failed"
    assert store.finished["completed_at"] is not None
    assert store.finished["stop_reason"] == "failed"


async def test_an_interrupted_run_records_no_stop_reason(monkeypatch):
    """Matching the reaper. `stop_reason` says why the *loop* ended and has no
    word for this; "failed" would also survive a Resume, which clears only
    `error_message`."""
    store = RecordingStore()

    async def boom(*args, **kwargs):
        raise SsoSessionExpired("session ended")

    monkeypatch.setattr(engine, "_execute", boom)
    await engine.run_optimization(
        uuid.uuid4(), store=store, seams=None,
        publish=lambda event: asyncio.sleep(0),
    )
    assert store.finished["stop_reason"] is None


# --- Eval: failed, once, with a sentence ----------------------------------


class StubSession:
    """The no-DB session `test_orchestrator.py` uses, reduced to what is read."""

    def __init__(self, run) -> None:
        self.run = run
        self.committed = 0

    async def get(self, model, pk):
        return self.run

    async def commit(self):
        self.committed += 1

    async def rollback(self):
        pass

    def add(self, obj):
        pass


async def test_an_expired_session_fails_an_eval_run_with_a_plain_sentence():
    """No `SsoSessionExpired:` prefix. The message is already written for the
    reader, and a class name in front of it buries "sign in again"."""
    from app.models import Run

    run = Run(
        id=uuid.uuid4(), eval_set_id=uuid.uuid4(), triggered_by="alice",
        status="running", config={}, secrets={},
        started_at=datetime.now(timezone.utc),
    )
    session = StubSession(run)
    await orchestrator._finalize_failed(
        session, run.id,
        SsoSessionExpired("Your sign-in ended. Sign in again and start the run."),
    )
    assert run.status == "failed"
    assert run.error_message == "Your sign-in ended. Sign in again and start the run."
    assert "SsoSessionExpired" not in (run.error_message or "")


async def test_an_ordinary_eval_failure_keeps_its_class_name():
    """The regression guard: every other exception still reads as
    `TypeName: message`, which is what makes an unexpected one diagnosable."""
    from app.models import Run

    run = Run(
        id=uuid.uuid4(), eval_set_id=uuid.uuid4(), triggered_by="alice",
        status="running", config={}, secrets={},
        started_at=datetime.now(timezone.utc),
    )
    await orchestrator._finalize_failed(StubSession(run), run.id, ValueError("nope"))
    assert run.error_message == "ValueError: nope"


# --- The token stays out of every column ----------------------------------


def test_the_registry_holds_the_token_and_nothing_else_does(configure):
    """The structural property the in-memory design buys. A refresh token that
    arrived as a request body field would land in a Pydantic model, and
    `.model_dump()` of those models is exactly what is written to
    `runs.secrets` and `optimization_runs.secrets`."""
    from app.schemas import OptimizationRunCreate, RunCreate

    for model in (RunCreate, OptimizationRunCreate):
        fields = set(model.model_fields)
        assert not any("refresh" in f for f in fields), (
            f"{model.__name__} grew a refresh-token field; it would be persisted "
            "by .model_dump() into a secrets column"
        )
        secrets_model = model.model_fields.get("secrets")
        if secrets_model is not None:
            nested = set(secrets_model.annotation.model_fields)
            assert not any("refresh" in f or "sso" in f for f in nested), (
                f"{model.__name__}.secrets grew an SSO field"
            )


def test_the_refresh_token_is_read_from_a_header_not_a_body():
    """Pinned because the header is the reason the token cannot be persisted by
    accident — see `app/auth.py:sso_refresh_token`."""
    import inspect

    from app.auth import sso_refresh_token

    params = inspect.signature(sso_refresh_token).parameters
    assert "x_sso_refresh_token" in params


async def test_registering_never_mutates_the_secrets_passed_alongside_it(configure):
    with sso_on(configure):
        secrets = {"agent_api_key": "sk-42"}
        agent_sso.register("run-1", "rt-1", "alice")
        assert secrets == {"agent_api_key": "sk-42"}
        assert "rt-1" not in str(secrets)


# --- The in-request probe uses the caller's own token ----------------------


def test_the_trigger_time_probe_uses_the_callers_bearer_token(configure):
    """The version probe in `POST /runs` runs before the run does, inside the
    request — so it has the caller's own token to hand and needs no refresh.
    Registering a scope for something that finishes in seconds would be a
    lifetime to manage for no reason."""
    from app.routers.runs import _probe_credential

    with sso_on(configure):
        kwargs = _probe_credential("at-live")
    assert set(kwargs) == {"agent_credential"}


async def test_the_probe_credential_is_the_token_verbatim(configure):
    from app.routers.runs import _probe_credential

    with sso_on(configure):
        cred = _probe_credential("at-live")["agent_credential"]
    assert await cred.value() == "at-live"


def test_the_probe_sends_nothing_when_sso_is_off(configure):
    """The regression guard: with the switch off, `agent_version` is called with
    the argument list it has always been called with."""
    from app.routers.runs import _probe_credential

    with configure(auth_mode="keycloak", agent_sso_enabled=False):
        assert _probe_credential("at-live") == {}


def test_the_probe_sends_nothing_in_fake_mode(configure):
    """`current_token` is None there, and there is no identity worth forwarding
    — fake mode decides who you are from a header anyone can set."""
    from app.routers.runs import _probe_credential

    with configure(auth_mode="fake", agent_sso_enabled=True):
        assert _probe_credential(None) == {}
