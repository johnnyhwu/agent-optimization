"""The four checks that answer "what can this agent server do?".

Three screens ask that question — the Run-eval dialog, the playground's connect
bar and the optimization wizard — and they used to answer it differently, so the
same agent could look ready on one and broken on another. The checks live here
once; what differs between the screens is only which failures *block*, and that
belongs to the screens.

The four:

    skills    the skills endpoint returns a usable listing      one GET, free
    chat      the chat endpoint answers                         one real call
    override  the skill override actually took effect           same call
    trace     that call's trace can be read back from Langfuse  a read, delayed

**Why `override` is checked here rather than only before a run.** An agent that
accepts `skill_studio.skills` and ignores it produces a *successful* optimization
run whose accuracy curve is flat, an hour later. The engine's own pre-flight
catches it (`optimizer/engine.py`), but only once the run exists and money is
being spent. This is the same question asked in two seconds, from a form.

**Why one call answers both `chat` and `override`.** They are layers of one
result, not two requests: no answer at all is a dead endpoint; an answer without
the magic value is an override that did not land. Sending two questions would
double the cost to learn nothing extra.

**Why the magic value is random per probe.** A constant would eventually be
hard-coded — by a well-meaning implementer making the check pass, or by a cache
— and a check that can be satisfied without reading the file we sent is not a
check. `secrets.token_hex` costs nothing and closes that.

Both probes go through `build_seams`, never straight to the HTTP clients, so a
deployment running on fake seams probes the fake ones. Reaching past the seam
would make the demo report a connection failure against an agent it was never
supposed to have.
"""
from __future__ import annotations

import asyncio
import json
import secrets as _secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import settings
from app.integrations import build_seams
from app.integrations.base import NotReady, WorkspaceOverride
from app.integrations.real.agent import AgentHttpError, build_payload

# The probe skill's path. Namespaced so it cannot collide with a real skill, and
# recognisable in an agent's logs as something the platform sent.
PROBE_SKILL_PATH = "skill_studio_probe/SKILL.md"

PROBE_QUESTION = (
    "Read the skill_studio_probe skill and tell me the magic value it gives."
)

# How long a preview shown in the UI may be. Long enough to see the shape of a
# request and the beginning of an answer; short enough that a skills map with a
# hundred files does not become the response body of this endpoint.
PREVIEW_CHARS = 4000

# Langfuse ingestion is asynchronous, so a trace read immediately after the call
# that produced it is routinely not there yet. That delay is the only false
# negative this check has, and it would be the one people hit most — so it is
# retried rather than reported. Three attempts over ~6s: long enough for a
# healthy pipeline, short enough to sit in front of a button.
TRACE_ATTEMPTS = 3
TRACE_RETRY_DELAY_S = 2.0


def _preview(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False)
    return text[:PREVIEW_CHARS]


@dataclass
class CheckResult:
    """One check's answer, in the shape the UI renders.

    `ok is None` means "not attempted" — which is a third state on purpose:
    "we did not check whether the override applied" and "the override did not
    apply" must not look the same on a screen that gates a run on the second.
    """

    ok: bool | None = None
    detail: str = ""
    error: str = ""


@dataclass
class ChatProbeResult:
    chat: CheckResult = field(default_factory=CheckResult)
    override: CheckResult = field(default_factory=CheckResult)
    trace: CheckResult = field(default_factory=CheckResult)
    trace_id: str = ""
    latency_ms: int | None = None
    request_preview: str = ""
    response_preview: str = ""


@dataclass
class SkillsProbeResult:
    skills: CheckResult = field(default_factory=CheckResult)
    version: str = ""
    paths: list[str] = field(default_factory=list)
    request_preview: str = ""
    response_preview: str = ""


def make_probe_skill() -> tuple[dict[str, str], str, str]:
    """`(skills map, question, magic value)` for one override check."""
    magic = f"XYZZY-{_secrets.token_hex(4).upper()}"
    skills = {
        PROBE_SKILL_PATH: (
            "---\n"
            "name: skill_studio_probe\n"
            "description: A connection check sent by Skill Studio.\n"
            "---\n"
            "# Probe skill\n\n"
            "When asked for the magic value, answer with exactly this token and "
            f"nothing else: {magic}\n"
        )
    }
    return skills, PROBE_QUESTION, magic


async def probe_skills(
    skills_url: str, timeout_s: float | None = None
) -> SkillsProbeResult:
    """Can the skills endpoint be read, and what does it hold?

    A blank URL is `ok=None`, not a failure: not configuring a skills endpoint
    is a supported choice, and calling it an error would put a red mark in front
    of someone running an evaluation that does not need one.
    """
    result = SkillsProbeResult()
    url = (skills_url or "").strip()
    result.request_preview = f"GET {url}" if url else ""

    try:
        seams = build_seams(
            {
                "agent_skills_url": url,
                "agent_timeout_s": timeout_s or settings.agent_probe_timeout_s,
            },
            include_workspace=True,
        )
    except Exception as exc:  # noqa: BLE001 - misconfiguration, reported as the check
        result.skills = CheckResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        return result

    if seams.workspace is None:
        # No client because no URL — the entry-level configuration, not an
        # error. `ok=None` is what keeps it out of the red on every screen that
        # does not need it.
        result.skills = CheckResult(ok=None, detail="No skills endpoint configured.")
        return result

    try:
        workspace = await seams.workspace.get_workspace()
    except Exception as exc:  # noqa: BLE001 - the agent server's problem, quoted as-is
        # The client's own message already names what was tried and what came
        # back; wrapping it would say "could not reach the agent" twice.
        result.skills = CheckResult(ok=False, error=str(exc) or type(exc).__name__)
        return result

    result.version = workspace.version
    result.paths = sorted(workspace.skills)
    result.skills = CheckResult(
        ok=True,
        detail=(
            f"{len(workspace.skills)} skill file"
            f"{'' if len(workspace.skills) == 1 else 's'}"
        ),
    )
    result.response_preview = _preview(
        {
            "version": workspace.version,
            # The paths, not the file bodies: a workspace is routinely hundreds
            # of kilobytes and none of it helps someone checking a connection.
            "skills": {p: f"<{len(workspace.skills[p])} chars>" for p in result.paths},
        }
    )
    return result


async def _check_trace(trace_client, trace_id: str) -> CheckResult:
    """Did the agent write this call's trace under the id we gave it?

    Retried, because Langfuse ingestion is asynchronous and the first read after
    a call is expected to miss. `NotReady` is the retryable answer; an exception
    is the trace store itself being unreachable, which is a different sentence
    and not worth retrying here.
    """
    if trace_client is None:
        return CheckResult(ok=None, detail="No trace store configured.")
    for attempt in range(TRACE_ATTEMPTS):
        try:
            trace = await trace_client.fetch_trace(trace_id)
        except Exception as exc:  # noqa: BLE001 - the trace store's problem, reported as its own
            return CheckResult(
                ok=False, error=f"could not read the trace store: {exc}"
            )
        if not isinstance(trace, NotReady):
            n = len(getattr(trace, "spans", []) or [])
            return CheckResult(
                ok=True, detail=f"trace found ({n} span{'' if n == 1 else 's'})"
            )
        if attempt < TRACE_ATTEMPTS - 1:
            await asyncio.sleep(TRACE_RETRY_DELAY_S)
    return CheckResult(
        ok=False,
        error=(
            f"no trace with id {trace_id} appeared in the trace store after "
            f"{TRACE_ATTEMPTS} attempts. Either the agent generated its own "
            "trace id instead of using the one it was given, or nothing was "
            "written."
        ),
    )


async def probe_chat(
    chat_url: str,
    timeout_s: float | None = None,
    *,
    with_override: bool = True,
    trace_client=None,
) -> ChatProbeResult:
    """One real call, read in three layers.

    `with_override=False` asks the cheapest useful question — "is anything
    listening that answers like a chat endpoint?" — and leaves `override`
    unattempted. With it on, the same call also proves the override landed.
    """
    result = ChatProbeResult()
    url = (chat_url or "").strip()
    if not url and settings.agent_impl == "real":
        result.chat = CheckResult(ok=False, error="No chat endpoint configured.")
        return result

    trace_id = uuid.uuid4().hex
    result.trace_id = trace_id
    budget = timeout_s or settings.agent_timeout_s

    skills, question, magic = make_probe_skill()
    override = WorkspaceOverride(skills=skills) if with_override else None
    if not with_override:
        question = "Reply with the single word: ok"

    result.request_preview = _preview(
        {
            "POST": url,
            "body": build_payload(
                question, trace_id, "skill-studio-probe", ["probe"], budget, override
            ),
        }
    )

    seams = build_seams({"agent_chat_url": url, "agent_timeout_s": budget})
    started = time.monotonic()
    try:
        answer = await seams.agent.call(
            question, trace_id, "skill-studio-probe", ["probe"], override
        )
    except (AgentHttpError, httpx.HTTPError) as exc:
        result.latency_ms = int((time.monotonic() - started) * 1000)
        result.chat = CheckResult(ok=False, error=str(exc) or type(exc).__name__)
        return result

    result.latency_ms = answer.latency_ms
    result.response_preview = _preview(answer.response or answer.error or "")

    if answer.failed:
        result.chat = CheckResult(ok=False, error=answer.error or "the call failed")
        return result

    result.chat = CheckResult(
        ok=True,
        detail=f"answered in {(answer.latency_ms or 0) / 1000:.1f}s",
    )

    if with_override:
        if magic in answer.response:
            result.override = CheckResult(ok=True, detail="override applied")
        else:
            # Deliberately not phrased as "you did not implement the override":
            # a refusal, a tool that failed to load, or a pipeline that strips
            # skill text all land here too, and a screen that accuses the first
            # sends people to fix the wrong thing.
            result.override = CheckResult(
                ok=False,
                error=(
                    "the agent answered, but its answer did not contain the "
                    "value from the skill file we sent. Either the skills "
                    "override was not applied, something in the prompt pipeline "
                    "dropped the file's contents, or the agent declined to "
                    "follow it."
                ),
            )

    if trace_client is not None:
        result.trace = await _check_trace(trace_client, trace_id)

    return result
