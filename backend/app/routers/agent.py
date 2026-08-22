"""The agent server pre-flight, for the "Run eval" dialog (§9.2 / §10.2).

One endpoint, and it exists because of where the dialog puts its connection
settings: behind a disclosure that is closed by default. The ordinary way to
start a run is therefore to press the button without ever opening them, and a
mistyped base URL used to be discovered by a run — a run row, a full set of
`question_results`, and one agent call per question, all spent finding out that
nothing was listening. This turns that into a mark on the dialog before anything
has been started.

**It is the same call the playground connects with.** `get_workspace` reaching
the agent proves the host is there, that it speaks the §17.3 contract, and hands
back the skill list the dialog's coverage check needs — all in one round trip. A
health endpoint of its own would prove less and be one more thing to keep in
step with the contract.

Two things are deliberately not reused from the run's own settings:

* **The timeout.** `AGENT_TIMEOUT_S` is the budget for answering a question, and
  it is two minutes by default. The Start button is disabled until this endpoint
  answers, so borrowing that budget would let one hung agent make the dialog
  unusable for as long as anyone is willing to wait. `AGENT_PROBE_TIMEOUT_S` is
  its own, and short — the same reasoning as the playground's `BASELINE_TIMEOUT_S`.
* **Which agent.** The base URL is a parameter, never an environment read. A
  probe that always asked the deployment's default could go green against one
  agent while the run went to another, which looks exactly like a check that
  passed.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import current_subject
from app.config import settings
from app.integrations import build_seams
from app.schemas import AgentSkillsOut
from app.services.agent_skills import top_level_skills

router = APIRouter(prefix="/agent", tags=["agent"])


def _workspace_client(agent_base_url: str = ""):
    """The workspace seam for one agent, or a 503 explaining why there isn't one.

    `include_workspace=True` is what constructs it at all: a misconfigured
    workspace seam must never be able to break the eval path, so only the
    endpoints that answer for it ask for one. `WORKSPACE_IMPL=real` with no agent
    base URL anywhere raises here, and that sentence is what the developer needs
    to read instead of a 500.
    """
    try:
        seams = build_seams(
            {
                "agent_base_url": agent_base_url,
                "agent_timeout_s": settings.agent_probe_timeout_s,
            },
            include_workspace=True,
        )
    except Exception as exc:  # noqa: BLE001 - misconfiguration, not a server bug
        raise HTTPException(status_code=503, detail=f"{type(exc).__name__}: {exc}") from exc
    if seams.workspace is None:  # pragma: no cover - include_workspace ensures one
        raise HTTPException(status_code=503, detail="no workspace client configured")
    return seams.workspace


@router.get("/skills", response_model=AgentSkillsOut)
async def agent_skills(
    # `Annotated` rather than a bare `Query(default)`, so the tests can call this
    # as a plain function and get the default rather than a FastAPI object.
    agent_base_url: Annotated[str, Query(description="blank uses the server's own")] = "",
    subject: str = Depends(current_subject),
):
    """Which skills this agent has — and, by answering at all, that it is there.

    A failure is a 503 carrying the agent server's own words, never an empty
    skill list. "This agent has no skills" is a warning about the eval set;
    "the agent server refused us" is a blocked Start button; and the only thing
    that can tell the two apart is the reason the server gave.
    """
    client = _workspace_client(agent_base_url)
    # What the run would resolve to, by the same rule `run_config.resolve` uses,
    # so the dialog names the agent that answered rather than the box that was
    # left blank.
    effective_url = (agent_base_url or "").strip() or settings.agent_base_url
    try:
        workspace = await client.get_workspace()
    except Exception as exc:  # noqa: BLE001
        # The reason, unwrapped. The workspace client's own messages already name
        # what it tried and what came back ("could not reach the agent server at
        # …/get_workspace", "agent server returned 404 for …"), and the dialog
        # prints its own heading above this — a prefix here only made the line
        # say "could not reach the agent server" twice.
        raise HTTPException(
            status_code=503, detail=str(exc) or type(exc).__name__
        ) from exc

    return AgentSkillsOut(
        agent_base_url=effective_url,
        version=workspace.version,
        skills=top_level_skills(workspace.skills),
    )
