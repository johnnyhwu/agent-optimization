"""Real SkillClient: read the agent server's skill catalogue (§10.2).

Two endpoints, both additive on the agent server's side and both read-only:

    GET {base}/skills          -> {"skills": [{"name", "description"}]}
    GET {base}/skills/{name}   -> {"name", "content"}

Writing a skill back is deliberately absent — that needs versioning and rollback
(§4.9) and belongs to Stage 3.

Parsing is tolerant in the same spirit as `real/agent.py`: this platform does not
own the agent server, and a catalogue that arrives as a bare list, or a skill
whose text is under `text` instead of `content`, is still perfectly usable
information. What is *not* tolerated is silence — an unreadable body raises with
the body quoted, because "no skills found" and "your URL is wrong" must not look
the same in the UI.

Shares `AGENT_BASE_URL` / `AGENT_TIMEOUT_S` with the agent seam: the skills live
on the same server that answers questions, so a second base URL would only be an
extra thing to get wrong.
"""
from __future__ import annotations

from typing import Any

import httpx

from app.config import settings
from app.integrations.base import Skill, SkillSummary


class SkillFetchError(RuntimeError):
    """The agent server could not be reached, or answered unusably."""


def _as_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _summary_from(entry: Any) -> SkillSummary | None:
    """One catalogue entry: a dict with a name, or just the name as a string."""
    if isinstance(entry, str):
        return SkillSummary(name=entry)
    if isinstance(entry, dict):
        name = _as_str(entry.get("name")) or _as_str(entry.get("skill"))
        if not name:
            return None
        return SkillSummary(
            name=name,
            description=_as_str(entry.get("description")) or _as_str(entry.get("summary")),
        )
    return None


def _entries_from(body: Any) -> list[Any] | None:
    """The list of skills out of the response body, wrapped or bare."""
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in ("skills", "items", "data"):
            value = body.get(key)
            if isinstance(value, list):
                return value
    return None


def _content_from(body: Any) -> str | None:
    if isinstance(body, str):
        return body
    if isinstance(body, dict):
        for key in ("content", "text", "skill", "body"):
            value = _as_str(body.get(key))
            if value is not None:
                return value
    return None


class HttpSkillClient:
    """Read the agent server's skills over HTTP."""

    def __init__(self, base_url: str | None = None, timeout_s: float | None = None) -> None:
        self.base_url = (base_url or settings.agent_base_url).rstrip("/")
        if not self.base_url:
            raise RuntimeError(
                "SKILL_IMPL=real but no agent base URL was given — set it in the "
                "playground config, or via AGENT_BASE_URL (e.g. http://agent-host:8080)."
            )
        self.timeout_s = timeout_s or settings.agent_timeout_s

    async def _get(self, path: str) -> Any:
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_s, follow_redirects=True
            ) as client:
                resp = await client.get(f"{self.base_url}{path}")
        except httpx.HTTPError as exc:
            raise SkillFetchError(
                f"could not reach the agent server at {self.base_url}{path}: {exc}"
            ) from exc

        if resp.status_code >= 400:
            raise SkillFetchError(
                f"agent server returned {resp.status_code} for {path}: {resp.text[:200]}"
            )
        try:
            return resp.json()
        except ValueError:
            # A skill's text is plausibly served as plain text; a catalogue is not.
            return resp.text

    async def list_skills(self) -> list[SkillSummary]:
        body = await self._get("/skills")
        entries = _entries_from(body)
        if entries is None:
            raise SkillFetchError(
                f"GET {self.base_url}/skills did not return a list of skills: "
                f"{str(body)[:200]}"
            )
        summaries = [s for s in (_summary_from(e) for e in entries) if s is not None]
        # An empty catalogue is a legitimate answer (an agent with no skills yet);
        # a catalogue of entries none of which had a name is not.
        if entries and not summaries:
            raise SkillFetchError(
                f"GET {self.base_url}/skills returned {len(entries)} entries, none "
                f"with a usable name: {str(body)[:200]}"
            )
        return summaries

    async def get_skill(self, name: str) -> Skill:
        body = await self._get(f"/skills/{name}")
        content = _content_from(body)
        if content is None:
            raise SkillFetchError(
                f"GET {self.base_url}/skills/{name} had no skill text in it: "
                f"{str(body)[:200]}"
            )
        description = None
        if isinstance(body, dict):
            description = _as_str(body.get("description"))
        return Skill(name=name, content=content, description=description)
