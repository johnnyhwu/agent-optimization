"""FastAPI application entrypoint."""
from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_subject
from app.config import settings
from app.db import get_session
from app.models import EvalSetRole
from app.routers import diagnosis, eval_sets, questions, results, runs
from app.services import run_config

app = FastAPI(title="Agent Eval — Stage 1 POC")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(eval_sets.router)
app.include_router(questions.router)
app.include_router(runs.router)
app.include_router(results.router)
app.include_router(diagnosis.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/run-config/defaults")
async def run_config_defaults(subject: str = Depends(current_subject)):
    """Prefill values for the run-config dialog, plus which seams are live.

    Only the non-secret settings — credentials are write-only, so the form starts
    them blank and the developer either types them or borrows an earlier run's.
    `impls` lets the dialog grey out the seams still set to `fake`, whose
    connection settings would have no effect.
    """
    return {
        "defaults": run_config.defaults(),
        "impls": {
            "agent": settings.agent_impl,
            "judge": settings.judge_impl,
            "trace": settings.trace_impl,
            "diagnosis": settings.diagnosis_impl,
        },
    }


@app.get("/users")
async def users(subject: str = Depends(current_subject)):
    """Fake user directory for the login switch + share pickers (§6.16)."""
    return {"users": settings.known_users, "current": subject}


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
