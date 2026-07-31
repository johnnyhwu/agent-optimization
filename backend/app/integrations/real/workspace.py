"""Real WorkspaceClient: read the agent server's config and skill files (§10.2).

Two endpoints, both additive on the agent server's side and both read-only:

    GET {base}/get_workspace       -> {"version", "config", "redacted_paths", "skills"}
    GET {base}/get_config_version  -> {"version"}

Writing a workspace back is deliberately absent — that needs versioning and
rollback (§4.9) and belongs to Stage 3.

**Why one request instead of a catalogue plus a fetch per skill**: a skill is a
directory, not a string, and `config.json` decides as much of the agent's
behaviour as the skill text does. Reading them separately would let the
playground pair this minute's config with last minute's skills and call the
result a snapshot — and the version string, which the whole staleness check
rests on, would have nothing single to describe.

Parsing is strict about the two things the UI cannot work without — `config`
being an object and `skills` being a flat {path: text} map — and tolerant about
everything else (a missing `redacted_paths`, a missing `version`). What is *not*
tolerated is silence: an unreadable body raises with the body quoted, because
"this agent has no skills" and "your URL is wrong" must not look the same in the
UI. A developer who cannot tell them apart retypes the skill from memory and
then tests the wrong text.

Shares `AGENT_BASE_URL` / `AGENT_TIMEOUT_S` with the agent seam: the workspace
lives on the same server that answers questions, so a second base URL would only
be an extra thing to get wrong.
"""
from __future__ import annotations

from typing import Any

import httpx

from app.config import settings
from app.integrations.base import Workspace


class WorkspaceFetchError(RuntimeError):
    """The agent server could not be reached, or answered unusably."""


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
    """Read the agent server's workspace over HTTP."""

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
        where = f"GET {self.base_url}/get_workspace"
        body = await self._get("/get_workspace")
        if not isinstance(body, dict):
            raise WorkspaceFetchError(f"{where} did not return an object: {str(body)[:200]}")

        config = body.get("config", {})
        if config is None:
            config = {}
        if not isinstance(config, dict):
            raise WorkspaceFetchError(
                f"{where} returned a 'config' that is not an object: {str(config)[:200]}"
            )

        redacted = body.get("redacted_paths") or []
        if not isinstance(redacted, list):
            redacted = []

        return Workspace(
            # A server that does not version its workspace still works; the
            # staleness check simply never fires. Refusing the whole snapshot
            # over a missing version would trade a real capability for a
            # convenience.
            version=_as_str(body.get("version")) or "",
            config=config,
            redacted_paths=[p for p in redacted if isinstance(p, str)],
            skills=_skills_from(body, where),
        )

    async def get_version(self) -> str:
        body = await self._get("/get_config_version")
        version = _as_str(body.get("version")) if isinstance(body, dict) else _as_str(body)
        if version is None:
            raise WorkspaceFetchError(
                f"GET {self.base_url}/get_config_version had no version in it: "
                f"{str(body)[:200]}"
            )
        return version
