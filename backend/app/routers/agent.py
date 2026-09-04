"""The agent server pre-flight, shared by every screen that names an agent.

Two endpoints, and they are split by **cost**, which is the fact that shapes
every screen using them:

    GET  /agent/skills      one GET against the skills endpoint. Free and fast,
                            so callers fire it while the developer is typing.
    POST /agent/chat-probe  one real question answered by a real model. Never
                            automatic; something has to ask for it.

The single check this replaces could be automatic because reading a skill
listing costs nothing. Now that the platform also has to prove the chat endpoint
answers — and, where it matters, that a skills override actually took effect —
the expensive half needs its own trigger. A GET that quietly spent a model call
on every keystroke would be indefensible.

**A missing skills endpoint is not a failure.** It is the entry-level
configuration, and evaluation runs against such an agent normally.
`GET /agent/skills` says so with `check.ok = null` rather than an error, and each
screen decides what that costs it — a lost coverage warning for an eval run, a
blocked wizard for an optimization, a read-only file list in the playground.

The reason any of this exists is where the Run-eval dialog puts its connection
settings: behind a disclosure that is closed by default. The ordinary way to
start a run is to press the button without ever opening them, and a mistyped URL
used to be discovered by a run — a run row, a full set of `question_results`, and
one agent call per question, all spent finding out that nothing was listening.

Two things are deliberately not reused from the run's own settings:

* **The timeout.** `AGENT_TIMEOUT_S` is the budget for answering a question, and
  it is two minutes by default. A skills read gets `AGENT_PROBE_TIMEOUT_S`
  instead — short, because a server that cannot list its files within a few
  seconds is not one to start a run against unnoticed, and because the Start
  button waits on it. The chat probe is the exception: it *is* a question, so it
  gets the question budget.
* **Which agent.** The URLs are parameters, never an environment read. A probe
  that always asked the deployment's default could go green against one agent
  while the run went to another, which looks exactly like a check that passed.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.auth import current_subject
from app.config import settings
from app.integrations import build_seams
from app.schemas import (
    AgentSkillsOut,
    ChatProbeIn,
    ChatProbeOut,
    CheckOut,
    ConformanceCaseOut,
    ConformanceIn,
    ConformanceOut,
)
from app.services.agent_conformance import run_conformance
from app.services.agent_probe import probe_chat, probe_skills
from app.services.agent_skills import top_level_skills

router = APIRouter(prefix="/agent", tags=["agent"])


@router.get("/skills", response_model=AgentSkillsOut)
async def agent_skills(
    # `Annotated` rather than a bare `Query(default)`, so the tests can call this
    # as a plain function and get the default rather than a FastAPI object.
    agent_skills_url: Annotated[str, Query(description="blank uses the server's own")] = "",
    subject: str = Depends(current_subject),
):
    """Which skills this agent has — and, by answering at all, that it is there.

    **200 in all three outcomes**, with the answer in `check.ok`: `true` for a
    listing, `false` carrying the agent server's own words for a failure, and
    `null` for "no skills endpoint was configured". That last one is why this no
    longer raises: a 503 for an unconfigured *optional* endpoint blocked
    evaluation against every agent that has only a chat endpoint — exactly the
    kind of agent this platform now sets out to accept.

    The distinction that must survive is between an empty listing and a broken
    one: "this agent has no skills" is a warning about the eval set, while "the
    agent server refused us" is a connection to fix, and only the reason the
    server gave can tell them apart. `check.error` carries it verbatim, because
    a summary here would flatten "no such host" and "401 from the agent" into
    the same unhelpful sentence.
    """
    # What a run would resolve to, by the same rule `run_config.resolve` uses, so
    # the dialog names the endpoint that answered rather than the box left blank.
    effective_url = (agent_skills_url or "").strip() or settings.agent_skills_url
    result = await probe_skills(effective_url, settings.agent_probe_timeout_s)
    return AgentSkillsOut(
        agent_skills_url=effective_url,
        version=result.version,
        # `top_level_skills` wants a workspace map; only the paths matter here,
        # and the file bodies are megabytes nobody on this screen reads.
        skills=top_level_skills({path: "" for path in result.paths}),
        check=CheckOut(**asdict(result.skills)),
        request_preview=result.request_preview,
        response_preview=result.response_preview,
    )


@router.post("/chat-probe", response_model=ChatProbeOut)
async def chat_probe(
    body: ChatProbeIn,
    subject: str = Depends(current_subject),
):
    """Ask this agent one question, and report what that proved.

    A POST because it has a cost — one real question answered by a real model —
    and because the trace check needs credentials, which have no business in a
    query string.

    `with_trace` builds the trace seam from the caller's own config and secrets,
    the same way a run does. Checking against the deployment's Langfuse while
    the run would use the caller's proves nothing about the run.

    Every failure is reported as a failed *check*, never as a 5xx. A 500 from
    this endpoint reads as a bug in this platform, and the whole point of the
    screen calling it is to say something about the agent instead.
    """
    config = body.config.model_dump()
    trace_client = None
    if body.with_trace:
        try:
            trace_client = build_seams(config, body.secrets.model_dump()).trace
        except Exception as exc:  # noqa: BLE001 - misconfiguration, not a server bug
            return ChatProbeOut(
                trace=CheckOut(ok=False, error=f"{type(exc).__name__}: {exc}")
            )

    try:
        result = await probe_chat(
            config.get("agent_chat_url") or settings.agent_chat_url,
            config.get("agent_timeout_s") or settings.agent_timeout_s,
            with_override=body.with_override,
            trace_client=trace_client,
        )
    except RuntimeError as exc:  # a seam that could not be built at all
        return ChatProbeOut(chat=CheckOut(ok=False, error=str(exc)))

    return ChatProbeOut(
        chat=CheckOut(**asdict(result.chat)),
        override=CheckOut(**asdict(result.override)),
        trace=CheckOut(**asdict(result.trace)),
        trace_id=result.trace_id,
        latency_ms=result.latency_ms,
        request_preview=result.request_preview,
        response_preview=result.response_preview,
    )


@router.post("/conformance", response_model=ConformanceOut)
async def conformance(
    body: ConformanceIn,
    subject: str = Depends(current_subject),
):
    """Run the whole acceptance checklist against one agent server.

    For someone who has just written a server and has nothing to point it at
    yet. It costs several model calls, which is why it is a page of its own with
    a button on it rather than anything that happens on the way past.

    Three of the cases it runs are unreachable from ordinary use — an empty
    skills map, a traversing path, an override that outlives its request — and
    those are the ones implementations get wrong, because each produces a
    correct-looking answer right up until it matters.
    """
    report = await run_conformance(
        body.agent_chat_url,
        body.agent_skills_url,
        body.agent_timeout_s or settings.agent_timeout_s,
    )
    return ConformanceOut(
        tier=report.tier,
        summary=report.summary,
        cases=[
            ConformanceCaseOut(
                id=c.id, title=c.title, why=c.why,
                result=CheckOut(**asdict(c.result)),
            )
            for c in report.cases
        ],
    )
