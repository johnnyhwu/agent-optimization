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

import httpx
import pytest
import respx

from app import agent_sso, orchestrator
from app.integrations import build_seams
from app.agent_sso import SsoSessionExpired
from app.optimizer import adapter, engine
from app.optimizer.store import Item


@pytest.fixture(autouse=True)
def _clean_registry():
    agent_sso._entries.clear()
    yield
    agent_sso._entries.clear()


CHAT_URL = "https://agent.test/v1/chat/completions"
TOKEN_URL = "https://kc.test/auth/realms/tsmc/protocol/openid-connect/token"


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
#
# The version probe in `POST /runs` runs before the run does, inside the
# request, so it has the caller's own token to hand and needs no refresh —
# registering a scope for something that finishes in seconds would be a lifetime
# to manage for no reason. The rule itself and the eight other endpoints that
# share it live in `test_agent_sso_probes.py`; this is only the seam that ties
# it to a run.


def test_the_trigger_time_probe_uses_the_callers_bearer_token(configure):
    with sso_on(configure):
        assert set(agent_sso.probe_kwargs("at-live")) == {"agent_credential"}


# --- Refusing before the work starts --------------------------------------
#
# `register` is best-effort — it reports whether it stored anything — but
# *starting anyway* is not. An agent server that wants a token would get none,
# and the symptom is a full run whose every question fails with the real cause
# (a browser that sent no session) nowhere on the page. The four entry points
# refuse up front instead.


def test_no_refusal_when_sso_is_off(configure):
    """Every deployment that has not asked for this. The header is absent and
    that is not a problem, so nothing may refuse."""
    with configure(auth_mode="keycloak", agent_sso_enabled=False):
        assert agent_sso.refusal_reason(None) is None


def test_no_refusal_in_fake_mode(configure):
    with configure(auth_mode="fake", agent_sso_enabled=True):
        assert agent_sso.refusal_reason(None) is None


def test_a_session_means_no_refusal(configure):
    with sso_on(configure):
        assert agent_sso.refusal_reason("rt-1") is None


def test_sso_on_with_no_session_refuses(configure):
    with sso_on(configure):
        reason = agent_sso.refusal_reason(None)
    assert reason is not None
    # The message has to name both ways out, because which one applies depends
    # on something the reader knows and the server does not.
    assert "sign in again" in reason.lower()
    assert "api key" in reason.lower()


def test_a_typed_key_is_enough_on_its_own(configure):
    """The escape hatch again: a deployment can forward identities *and* have
    one agent behind a gateway that wants its own key. Where there is a
    credential to send there is nothing to refuse."""
    with sso_on(configure):
        assert agent_sso.refusal_reason(None, "sk-typed") is None


def test_the_deployment_key_also_satisfies_it(configure):
    with sso_on(configure):
        assert agent_sso.refusal_reason(None, "sk-env") is None


def test_whitespace_is_neither_a_session_nor_a_key(configure):
    with sso_on(configure):
        assert agent_sso.refusal_reason("  ") is not None
        assert agent_sso.refusal_reason(None, "   ") is not None


def test_every_entry_point_applies_the_refusal():
    """Named rather than exercised: each of the four is a different router with
    its own session and body, and what matters is that none was forgotten. A
    fifth entry point added later without this line is what the assertion
    message is for."""
    import inspect

    from app.routers import optimization, playground, runs

    sources = {
        "trigger_run": inspect.getsource(runs.trigger_run),
        "create_attempt": inspect.getsource(playground.create_attempt),
        "create_optimization_run": inspect.getsource(optimization.create_optimization_run),
        "resume_optimization_run": inspect.getsource(optimization.resume_optimization_run),
    }
    for name, src in sources.items():
        assert "refusal_reason" in src, (
            f"{name} starts long-running agent work but does not check "
            "agent_sso.refusal_reason, so under SSO it can start a run that "
            "fails every question"
        )
        assert "agent_sso.register" in src, f"{name} never registers a session"


# --- End to end: registry -> build_seams -> a real request ----------------
#
# Every part above is tested on its own. This is the join: a token registered on
# one side has to come out of an HTTP request on the other, refresh itself
# halfway through, and take the run down cleanly when the session ends. A chain
# whose links are each correct can still not be connected.


@respx.mock
async def test_a_registered_session_reaches_the_agent_as_a_bearer_header(configure):
    seen: list[str] = []

    def record(request):
        seen.append(request.headers.get("authorization", ""))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": "42"}, "finish_reason": "stop"}
                ]
            },
        )

    respx.post(CHAT_URL).mock(side_effect=record)
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200, json={"access_token": "at-1", "expires_in": 600, "token_type": "Bearer"}
        )
    )

    with sso_on(configure, agent_impl="real", agent_chat_url=CHAT_URL, agent_api_key=""):
        agent_sso.register("run-1", "rt-1", "alice")
        seams = build_seams({"agent_chat_url": CHAT_URL}, {}, **agent_sso.seam_kwargs("run-1"))
        answer = await seams.agent.call("q", "c1", "alice")

    assert answer.response == "42"
    assert seen == ["Bearer at-1"]


@respx.mock
async def test_the_token_is_re_minted_partway_through_a_run(configure):
    """The whole point of the feature. A 10-minute token and an hours-long run:
    the second question has to carry a token the first one did not."""
    seen: list[str] = []

    def record(request):
        seen.append(request.headers.get("authorization", ""))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
                ]
            },
        )

    respx.post(CHAT_URL).mock(side_effect=record)
    respx.post(TOKEN_URL).mock(
        side_effect=[
            # Inside the 180s margin, so the next question re-mints.
            httpx.Response(200, json={"access_token": "at-1", "expires_in": 30}),
            httpx.Response(200, json={"access_token": "at-2", "expires_in": 600}),
        ]
    )

    with sso_on(configure, agent_impl="real", agent_chat_url=CHAT_URL, agent_api_key=""):
        agent_sso.register("run-1", "rt-1", "alice")
        seams = build_seams({"agent_chat_url": CHAT_URL}, {}, **agent_sso.seam_kwargs("run-1"))
        await seams.agent.call("q", "c1", "alice")
        await seams.agent.call("q", "c2", "alice")

    assert seen == ["Bearer at-1", "Bearer at-2"], (
        "the second question reused the first one's token, which is exactly the "
        "mid-run expiry this feature exists to prevent"
    )


@respx.mock
async def test_an_ended_session_raises_out_of_the_agent_call(configure):
    """It must reach the run's own handler, not be swallowed as an agent error:
    `optimizer/engine.py` and `orchestrator.py` both branch on the type."""
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json={}))
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant"})
    )

    with sso_on(configure, agent_impl="real", agent_chat_url=CHAT_URL, agent_api_key=""):
        agent_sso.register("run-1", "rt-1", "alice")
        seams = build_seams({"agent_chat_url": CHAT_URL}, {}, **agent_sso.seam_kwargs("run-1"))
        with pytest.raises(SsoSessionExpired):
            await seams.agent.call("q", "c1", "alice")


@respx.mock
async def test_an_ended_session_is_not_retried_against_the_provider(configure):
    """`with_retries` only retries `RETRYABLE`, so an expired session must fail
    on the first attempt rather than hammering the identity provider three times
    per question — which across a run is thousands of requests."""
    from app.pipeline import call_agent

    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json={}))
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant"})
    )

    with sso_on(configure, agent_impl="real", agent_chat_url=CHAT_URL, agent_api_key=""):
        agent_sso.register("run-1", "rt-1", "alice")
        seams = build_seams({"agent_chat_url": CHAT_URL}, {}, **agent_sso.seam_kwargs("run-1"))
        with pytest.raises(SsoSessionExpired):
            await call_agent(seams, "q", "c1", "alice", [], 30.0, asyncio.Event())

    assert route.call_count == 1


@respx.mock
async def test_with_sso_off_the_request_carries_no_credential_at_all(configure):
    """The switched-off path, proved on the wire rather than argued about."""
    seen: list[bool] = []

    def record(request):
        seen.append("authorization" in request.headers)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
                ]
            },
        )

    respx.post(CHAT_URL).mock(side_effect=record)
    with configure(
        auth_mode="keycloak", agent_sso_enabled=False,
        agent_impl="real", agent_chat_url=CHAT_URL, agent_api_key="", agent_auth_header="",
    ):
        agent_sso.register("run-1", "rt-1", "alice")
        seams = build_seams({"agent_chat_url": CHAT_URL}, {}, **agent_sso.seam_kwargs("run-1"))
        await seams.agent.call("q", "c1", "alice")

    assert seen == [False]


# --- The rollout gather ---------------------------------------------------
#
# The two tests above prove `engine.run_optimization` routes an expired session
# to `interrupted`, but they raise from a stubbed `_execute` — so nothing in
# them travels the path the exception actually takes. That path runs through
# `adapter.run_rollout`, which gathers its items with `return_exceptions=True`
# so that one unexpected per-item error cannot cancel the rest of the split.
# An expired session arrived there as an exception like any other and was
# written into a `failed` row, which meant the handler two frames up was never
# reached: the run ended `failed`, not resumable, discarding however many hours
# of finished steps — the exact outcome that handler exists to prevent.
#
# Exercised against the real `run_rollout` for that reason. A stub of it would
# pass whatever it was written to pass.


class _ExpiringSeams:
    """A seam set whose agent's session ends on the nth call.

    `after=0` expires immediately; a higher number lets that many items answer
    first, which is the realistic shape — a token dies partway through a split,
    not before it.
    """

    def __init__(self, after: int = 0):
        self.after = after
        self.calls = 0
        outer = self

        class _Agent:
            async def call(self, question, correlation_id, user_id, tags, workspace=None):
                from app.integrations.base import AgentResponse

                outer.calls += 1
                if outer.calls > outer.after:
                    raise SsoSessionExpired(
                        "your sign-in ended and could not be renewed; sign in again"
                    )
                return AgentResponse(
                    response="an answer", correlation_id=correlation_id, latency_ms=1
                )

        class _Judge:
            async def judge(self, question, response, ground_truth):
                from app.integrations.base import Verdict

                return Verdict(verdict="correct", score=1.0, comment="")

        class _Trace:
            async def fetch_trace(self, correlation_id):
                return None

        self.agent, self.judge, self.trace = _Agent(), _Judge(), _Trace()


def _items(count: int):
    return [
        Item(
            item_key=f"k{i}",
            question=f"q{i}",
            ground_truth_response="gt",
            ground_truth_reasoning="r",
        )
        for i in range(count)
    ]


@pytest.fixture
def _fast_traces(configure):
    """A trace is never going to land here, and waiting for one with backoff
    would make every test below spend its time asleep."""
    with configure(trace_poll_max_attempts=1, trace_poll_backoff_s=[0.0]):
        yield


async def test_an_expired_session_escapes_the_rollout_gather(_fast_traces):
    """The bug itself: `return_exceptions=True` must not swallow this one."""
    with pytest.raises(SsoSessionExpired):
        await adapter.run_rollout(
            _items(3),
            skill_files={"billing/SKILL.md": "# Billing\n1. Identify.\n"},
            mode="isolated",
            skill_name="billing",
            seams=_ExpiringSeams(),
            config={},
            concurrency=2,
        )


async def test_it_escapes_even_when_some_items_already_answered(_fast_traces):
    """Partway through is the realistic case, and the one where the rows that
    did land make a `failed` row look like just another agent error."""
    with pytest.raises(SsoSessionExpired):
        await adapter.run_rollout(
            _items(4),
            skill_files={"billing/SKILL.md": "# Billing\n1. Identify.\n"},
            mode="isolated",
            skill_name="billing",
            seams=_ExpiringSeams(after=2),
            config={},
            concurrency=1,
        )


async def test_the_message_the_run_ends_with_is_the_agent_sso_one(_fast_traces):
    """It becomes the run's `error_message`, so it has to survive intact rather
    than arrive wrapped in a row's "the agent refused" wording."""
    with pytest.raises(SsoSessionExpired) as caught:
        await adapter.run_rollout(
            _items(1),
            skill_files={"billing/SKILL.md": "# Billing\n1. Identify.\n"},
            mode="isolated",
            skill_name="billing",
            seams=_ExpiringSeams(),
            config={},
        )

    assert "sign in again" in str(caught.value)


async def test_an_ordinary_agent_error_is_still_a_failed_row(_fast_traces):
    """The regression guard for the re-raise above: every *other* exception
    keeps being one item's problem. A rollout that aborted on the first timeout
    would lose the whole split to one flaky question."""
    class _Boom(_ExpiringSeams):
        def __init__(self):
            super().__init__()

            class _Agent:
                async def call(self, question, correlation_id, user_id, tags, workspace=None):
                    raise RuntimeError("the agent server fell over")

            self.agent = _Agent()

    rows = await adapter.run_rollout(
        _items(2),
        skill_files={"billing/SKILL.md": "# Billing\n1. Identify.\n"},
        mode="isolated",
        skill_name="billing",
        seams=_Boom(),
        config={},
    )

    assert len(rows) == 2
    assert [r.status for r in rows] == ["failed", "failed"]


async def test_the_expiry_survives_a_step_and_lands_interrupted(monkeypatch, _fast_traces):
    """The two halves joined: an expiry raised inside a real `run_rollout` has
    to reach `run_optimization`'s handler and finalize the run as `interrupted`.

    The step around it is stubbed — the engine's own `_execute` needs a run row
    and a spec from the database — but the rollout inside it is the real one,
    which is the frame the swallowed exception never got past.
    """
    store = RecordingStore()

    async def execute(run_id, **kwargs):
        await adapter.run_rollout(
            _items(2),
            skill_files={"billing/SKILL.md": "# Billing\n1. Identify.\n"},
            mode="isolated",
            skill_name="billing",
            seams=_ExpiringSeams(),
            config={},
        )
        return "completed", None, "finished"

    monkeypatch.setattr(engine, "_execute", execute)
    await engine.run_optimization(
        uuid.uuid4(), store=store, seams=None,
        publish=lambda event: asyncio.sleep(0),
    )

    assert store.finished["status"] == "interrupted", (
        "an expiry inside the rollout must not be flattened into failed rows "
        "that let the step report success"
    )
    assert store.finished.get("completed_at") is None
