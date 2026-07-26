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
