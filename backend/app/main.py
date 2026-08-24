"""FastAPI application entrypoint."""
from __future__ import annotations

import contextlib
import datetime as dt
import logging

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_subject
from app.config import settings
from app.db import SessionLocal, get_session
from app.models import EvalSetRole, Run
from app.optimizer.runner import reap_interrupted_optimization_runs
from app.routers import (
    agent,
    diagnosis,
    eval_set_scripts,
    eval_sets,
    export,
    optimization,
    playground,
    questions,
    results,
    runs,
    user_settings as user_settings_router,
    users,
)
from app.services import run_config, user_settings

# Give the application's own loggers somewhere to go.
#
# Uvicorn configures handlers for the `uvicorn.*` loggers and leaves the root
# logger alone, so until this line every `logging.getLogger(__name__).info(...)`
# in this codebase — the orchestrator's, the pipeline's, the playground's — was
# formatted and then discarded, and nobody noticed because the only way to tell
# is to go looking for a line that should be there.
#
# It became load-bearing with the eval-set script runner: an audit record of who
# ran what against which database is the compensating control for the one thing
# an in-container sandbox cannot prevent (outbound network access), and a
# control that writes to a logger with no handler is not a control.
#
# `force=False` (the default) means a deployment that configures logging itself,
# or a test harness that has already installed handlers, wins.
logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

log = logging.getLogger(__name__)


async def reap_interrupted_runs(session_factory=None) -> int:
    """Close out runs this process can no longer be executing.

    A run is an `asyncio.create_task` background task in *this* process (§6.1).
    When the backend restarts — a deploy, a crash, an OOM kill — the task is
    gone but `runs.status` is still 'running', and nothing else will ever change
    it: the UI keeps waiting on a run that cannot finish, and `POST /cancel`
    rejects it as already terminal. Production hits this on its very first
    deploy.

    Reaping at startup is safe precisely because of the single-worker constraint
    (§5.3, §15.2): no other process could be legitimately running these. If that
    constraint is ever lifted, this has to move with it, or one worker booting
    will kill another worker's live run.

    Returns how many runs were closed out. `session_factory` is an injection
    point for the tests; production always uses the app's own.
    """
    async with (session_factory or SessionLocal)() as session:
        result = await session.execute(
            update(Run)
            .where(Run.status == "running")
            .values(
                status="failed",
                error_message="backend restarted; this run was interrupted",
                completed_at=dt.datetime.now(dt.timezone.utc),
            )
        )
        await session.commit()
        return result.rowcount or 0


def _log_script_limits() -> None:
    """Print the eval-set script ceilings once, at boot.

    They are settings an operator changes and then wants to confirm, and until
    now there was no way to confirm one short of triggering it. Worse, a
    misspelled `SCRIPT_*` in a `.env` is dropped without complaint — `Settings`
    is `extra="ignore"` — so a raised limit that never arrived looked exactly
    like a limit that was never raised. One line in the log answers it.
    """
    log.info(
        "eval-set script limits: max_queries=%s rows_per_query=%s "
        "statement_timeout_s=%s wall_clock_s=%s memory_mb=%s",
        settings.script_max_queries,
        settings.script_max_rows_per_query,
        settings.script_statement_timeout_s,
        settings.script_wall_clock_s,
        settings.script_memory_mb,
    )


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    _log_script_limits()
    await reap_interrupted_runs()
    # Its own reaper, and a different verdict: an optimization run is
    # checkpointed per step, so a restart leaves something resumable rather than
    # something to write off. See `app/optimizer/runner.py`.
    await reap_interrupted_optimization_runs()
    yield


# `root_path` is for a proxy that strips a prefix before forwarding (nginx
# `proxy_pass …:8000/` under `location /api/`). Routes are unaffected; what it
# fixes is the generated OpenAPI/docs URLs, which would otherwise point above the
# prefix and 404. Empty by default, so running the backend directly is unchanged.
app = FastAPI(
    title="Skill Studio",
    root_path=settings.root_path,
    lifespan=lifespan,
    # The built-in doc routes are unauthenticated; these are re-added below with
    # the same identity dependency as everything else.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # A cross-origin fetch can only read the safelisted response headers unless
    # the server says otherwise, and Content-Disposition is not one of them. The
    # export download reads it for the filename the server chose; without this
    # the browser still saves the file, but under a made-up name. Same-origin
    # deployments (nginx serving the UI and proxying /api) never hit this, which
    # is exactly why it would have gone unnoticed until someone ran the UI
    # against a backend on another host.
    expose_headers=["Content-Disposition"],
)

app.include_router(agent.router)
app.include_router(users.router)
app.include_router(eval_sets.router)
app.include_router(eval_set_scripts.router)
app.include_router(questions.router)
app.include_router(runs.router)
app.include_router(results.router)
app.include_router(export.router)
app.include_router(diagnosis.router)
app.include_router(playground.router)
app.include_router(optimization.router)
app.include_router(user_settings_router.router)


@app.get("/health")
async def health():
    """The one unauthenticated endpoint: container and proxy health probes."""
    return {"status": "ok"}


# --- API docs, behind the same identity check as the API itself -------------
# In fake mode this is transparent (the header defaults), so browsing /docs
# during development is unchanged. In keycloak mode it is effectively closed to
# browsers, since a plain navigation cannot set an Authorization header — that is
# the intent, and `curl -H "Authorization: Bearer …" …/openapi.json` is the way
# to read the schema from a deployment.
@app.get("/openapi.json", include_in_schema=False)
async def openapi_schema(subject: str = Depends(current_subject)):
    return app.openapi()


@app.get("/docs", include_in_schema=False)
async def swagger_ui(subject: str = Depends(current_subject)):
    return get_swagger_ui_html(
        openapi_url=f"{app.root_path}/openapi.json", title=f"{app.title} — docs"
    )


@app.get("/redoc", include_in_schema=False)
async def redoc(subject: str = Depends(current_subject)):
    return get_redoc_html(
        openapi_url=f"{app.root_path}/openapi.json", title=f"{app.title} — docs"
    )


@app.get("/run-config/defaults")
async def run_config_defaults(
    subject: str = Depends(current_subject),
    session: AsyncSession = Depends(get_session),
):
    """Prefill values for the run-config dialog, plus which seams are live.

    Only the non-secret settings — credentials are write-only, so the form starts
    them blank and the developer either types them or borrows an earlier run's.
    `impls` lets the dialog grey out the seams still set to `fake`, whose
    connection settings would have no effect. The playground's config panel is
    the same form and reads the same values.

    The defaults are this deployment's, with the caller's own saved settings laid
    over them (`services/user_settings.py`). **That overlay happens here and not
    in `run_config.defaults()`**, which `resolve()` also calls: prefilling a form
    is a convenience, and what a run executes with must not depend on who
    triggered it. `system` is sent alongside so the settings page's link can say
    whether anything was actually overridden.
    """
    return {
        "defaults": await user_settings.effective_run_defaults(session, subject),
        # What this deployment would have used. The dialog compares the two to
        # decide whether to say "prefilled from your defaults"; without it the
        # browser would have to keep its own copy of the environment to notice.
        "system_defaults": run_config.defaults(),
        # Deployment-level, and it overrides the verdict a judge prompt returns
        # (integrations/real/judge.py). Surfaced so the prompt editor can say so
        # — rewriting what "score" means while a threshold silently reinterprets
        # it is a trap worth one line of text.
        "judge_score_threshold": settings.judge_score_threshold,
        "impls": {
            "agent": settings.agent_impl,
            "judge": settings.judge_impl,
            "trace": settings.trace_impl,
            "diagnosis": settings.diagnosis_impl,
            # Drafts an expected process from a trace, on the shortlist's button.
            "synthesis": settings.synthesis_impl,
            # The playground's view of the agent's config + skills (§10.2).
            # Fake means what is shown is canned, which the editor says so
            # nobody edits a fake skill expecting the real agent to have it.
            "workspace": settings.workspace_impl,
            # Writes the skill edits in an optimization run. Fake means the
            # edits are canned, so the Optimize section is demonstrable on
            # Docker alone like every other part of the product.
            "optimizer": settings.optimizer_impl,
        },
    }


@app.get("/me")
async def me(
    subject: str = Depends(current_subject),
    session: AsyncSession = Depends(get_session),
):
    """Who am I + my role on each eval set (fake login, §6.16). Lets the UI show
    the current identity and gate owner-only actions."""
    rows = (
        await session.execute(
            select(EvalSetRole.eval_set_id, EvalSetRole.role).where(
                EvalSetRole.user_subject == subject
            )
        )
    ).all()
    return {
        "subject": subject,
        "roles": {str(eid): role for eid, role in rows},
    }
