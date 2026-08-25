"""Real WorkspaceClient: read the agent server's skill files.

One endpoint, read-only and additive on the agent server's side:

    GET {base}/skills  -> {"skills": {path: text}, "version": str (optional)}

Writing a workspace back is deliberately absent — that needs versioning and
rollback (§4.9) and belongs to Stage 3.

**Why one request rather than a catalogue plus a fetch per skill**: a skill is a
directory, not a string. Reading them separately would let the playground pair
this minute's `SKILL.md` with last minute's reference files and call the result a
snapshot — and the version string, which the whole staleness check rests on,
would have nothing single to describe.

**Why the version may be omitted.** Maintaining a string that has to move
whenever anything behavioural moves is the most forgettable requirement we could
put on an agent author, and one that has stopped moving disables the staleness
check without saying so. So it is optional, and `derived_version` fills in from
the skill files when it is absent. That fallback is strictly weaker — it cannot
see a model swap or a system-prompt edit — which is why a server that *can*
supply its own is asked to.

Parsing is strict about the one thing the UI cannot work without — `skills`
being a flat {path: text} map — and tolerant about the rest (an absent `skills`
is an agent with none; an absent `version` is derived). What is *not* tolerated
is silence: an unreadable body raises with the body quoted, because "this agent
has no skills" and "your URL is wrong" must not look the same in the UI. A
developer who cannot tell them apart retypes the skill from memory and then
tests the wrong text.

Shares `AGENT_BASE_URL` / `AGENT_TIMEOUT_S` with the agent seam: the skills live
on the same server that answers questions, so a second base URL would only be an
extra thing to get wrong.
"""
from __future__ import annotations

from typing import Any

import httpx

from app.config import settings
from app.integrations.base import Workspace, derived_version


class WorkspaceFetchError(RuntimeError):
    """The agent server could not be reached, or answered unusably."""


SKILLS_PATH = "/skills"


def _as_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _skills_from(body: dict, where: str) -> dict[str, str]:
    """The flat {relative path: text} map, or a failure naming what arrived.

    A skills value that is not a map of strings is a failure rather than an
    empty dict for the reason in the module docstring: an empty workspace is a
    legitimate answer and has to stay distinguishable from a broken one.
    """
    raw = body.get("skills", {})
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise WorkspaceFetchError(
            f"{where} returned a 'skills' that is not an object: {str(raw)[:200]}"
        )
    skills: dict[str, str] = {}
    for path, content in raw.items():
        if not isinstance(path, str) or not isinstance(content, str):
            raise WorkspaceFetchError(
                f"{where} returned a skill entry that is not path -> text: "
                f"{str(path)[:80]} -> {type(content).__name__}"
            )
        skills[path] = content
    return skills


class HttpWorkspaceClient:
    """Read the agent server's skill files over HTTP."""

    def __init__(self, base_url: str | None = None, timeout_s: float | None = None) -> None:
        self.base_url = (base_url or settings.agent_base_url).rstrip("/")
        if not self.base_url:
            raise RuntimeError(
                "WORKSPACE_IMPL=real but no agent base URL was given — set it in "
                "the playground config, or via AGENT_BASE_URL "
                "(e.g. http://agent-host:8080)."
            )
        self.timeout_s = timeout_s or settings.agent_timeout_s

    async def _get(self, path: str) -> Any:
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_s, follow_redirects=True
            ) as client:
                resp = await client.get(f"{self.base_url}{path}")
        except httpx.HTTPError as exc:
            raise WorkspaceFetchError(
                f"could not reach the agent server at {self.base_url}{path}: {exc}"
            ) from exc

        if resp.status_code >= 400:
            raise WorkspaceFetchError(
                f"agent server returned {resp.status_code} for {path}: {resp.text[:200]}"
            )
        try:
            return resp.json()
        except ValueError as exc:
            raise WorkspaceFetchError(
                f"GET {self.base_url}{path} did not return JSON: {resp.text[:200]}"
            ) from exc

    async def get_workspace(self) -> Workspace:
        where = f"GET {self.base_url}{SKILLS_PATH}"
        body = await self._get(SKILLS_PATH)
        if not isinstance(body, dict):
            raise WorkspaceFetchError(f"{where} did not return an object: {str(body)[:200]}")

        skills = _skills_from(body, where)
        return Workspace(
            # The server's own string wins: it can see changes we cannot. Ours
            # is the fallback, never the override.
            version=_as_str(body.get("version")) or derived_version(skills),
            skills=skills,
        )

    async def get_version(self) -> str:
        """The same read as `get_workspace`, reduced to its version.

        There is no endpoint of its own for this. One that existed would have to
        agree with the snapshot endpoint on every deploy, and the failure mode
        when it did not — a staleness check that answers about a different
        moment than the editor was filled from — is silent in both directions.
        """
        return (await self.get_workspace()).version
