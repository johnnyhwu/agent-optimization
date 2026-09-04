"""The whole acceptance checklist, run against one agent server.

`agent_probe` answers "can this platform use this agent?" — the question a form
asks on the way to doing something. This answers a different one: "did I
implement the contract correctly?", asked by someone who has just written a
server and has nothing to point it at yet.

The difference is what gets checked. Three of these cases are unreachable from
normal use and are exactly the ones implementations get wrong:

  * **An empty skills map.** `{}` means "run with no skills", and `{}` is falsy
    in every language anyone will write this in. The bug — falling back to the
    deployed files — produces a *correct-looking* answer, so nothing else ever
    catches it, and it silently ruins the one measurement an optimization run
    makes against no skill at all.
  * **Path traversal.** The keys are attacker-influenced strings that most
    implementations turn into filenames. Skill Studio never sends a hostile one,
    so this is only ever exercised by someone attacking the server, or here.
  * **Persistence.** An override written into the real skills directory works
    perfectly for one call and quietly corrupts the deployed agent. Nothing in
    a single call can see it; two calls can.

Each case is one HTTP request and its own sentence. A checklist that reported
"7 of 9 passed" would be a worse version of running the curl commands by hand —
what an implementer needs is which one, and what it means.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import httpx

from app.config import settings
from app.integrations.base import WorkspaceOverride
from app.integrations.real.agent import build_payload
from app.services.agent_probe import CheckResult, make_probe_skill, probe_skills


@dataclass
class ConformanceCase:
    """One numbered case from the acceptance checklist."""

    id: str
    title: str
    # What this case is for, in the implementer's terms — shown whether it
    # passed or failed, because "why does this matter" is the part a checklist
    # normally leaves out.
    why: str
    result: CheckResult = field(default_factory=CheckResult)


@dataclass
class ConformanceReport:
    cases: list[ConformanceCase] = field(default_factory=list)
    tier: int = 0
    summary: str = ""


async def _post(client: httpx.AsyncClient, url: str, body: dict) -> httpx.Response:
    return await client.post(url, json=body, headers={"Content-Type": "application/json"})


def _answer_of(resp: httpx.Response) -> str | None:
    """The assistant's text, or None if this is not a usable chat completion."""
    try:
        body = resp.json()
    except ValueError:
        return None
    choices = (body or {}).get("choices") if isinstance(body, dict) else None
    if not isinstance(choices, list) or not choices:
        return None
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            p.get("text", "")
            for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
    return None


async def run_conformance(
    chat_url: str, skills_url: str = "", timeout_s: float | None = None
) -> ConformanceReport:
    report = ConformanceReport()
    chat = (chat_url or "").strip()
    budget = timeout_s or settings.agent_timeout_s

    def case(cid, title, why, result):
        report.cases.append(ConformanceCase(id=cid, title=title, why=why, result=result))

    if not chat:
        case(
            "chat", "Chat endpoint answers",
            "Everything else needs an endpoint to talk to.",
            CheckResult(ok=False, error="No chat endpoint given."),
        )
        return _finish(report)

    skills_map, question, magic = make_probe_skill()

    async with httpx.AsyncClient(timeout=budget, follow_redirects=True) as client:
        # ① A plain call: the baseline everything else varies from.
        plain = build_payload(
            "Reply with the single word: ok", uuid.uuid4().hex,
            "skill-studio-conformance", ["conformance"], budget, None,
        )
        try:
            resp = await _post(client, chat, plain)
        except httpx.HTTPError as exc:
            case(
                "chat", "Chat endpoint answers",
                "Everything else needs an endpoint to talk to.",
                CheckResult(ok=False, error=str(exc)),
            )
            return _finish(report)

        answer = _answer_of(resp)
        if resp.status_code >= 400 or not (answer or "").strip():
            case(
                "chat", "Chat endpoint answers",
                "Everything else needs an endpoint to talk to.",
                CheckResult(
                    ok=False,
                    error=f"HTTP {resp.status_code}: {resp.text[:400]}",
                ),
            )
            return _finish(report)
        case(
            "chat", "Chat endpoint answers",
            "Everything else needs an endpoint to talk to.",
            CheckResult(ok=True, detail=f"answered: {answer.strip()[:80]}"),
        )

        # ④ The override is applied.
        with_override = build_payload(
            question, uuid.uuid4().hex, "skill-studio-conformance",
            ["conformance"], budget, WorkspaceOverride(skills=skills_map),
        )
        resp = await _post(client, chat, with_override)
        answer = _answer_of(resp) or ""
        applied = magic in answer
        case(
            "override", "The skills override is applied",
            "Optimization sends a candidate skill with every rollout. An agent "
            "that accepts the field and ignores it produces a run that completes "
            "and means nothing.",
            CheckResult(ok=True, detail="the sent skill was used")
            if applied
            else CheckResult(
                ok=False,
                error=(
                    "the answer did not contain the value from the skill we sent. "
                    "Either the override was not applied, the prompt pipeline "
                    "dropped the file's contents, or the agent declined. "
                    f"It said: {answer.strip()[:200]}"
                ),
            ),
        )

        # ⑤ An empty map means no skills — not "use your own".
        empty = build_payload(
            question, uuid.uuid4().hex, "skill-studio-conformance",
            ["conformance"], budget, WorkspaceOverride(skills={}),
        )
        resp = await _post(client, chat, empty)
        answer = _answer_of(resp) or ""
        case(
            "empty_skills", "An empty skills map means no skills",
            "`{}` is falsy in every language this gets written in, so `if "
            "skills:` silently turns \"run with no skills\" into \"use your "
            "own\" — and the answer still looks right.",
            CheckResult(ok=True, detail="answered without the sent skill")
            if magic not in answer
            else CheckResult(
                ok=False,
                error=(
                    "the answer still contained the previous call's skill value, "
                    "so an empty map did not clear the skills for this call."
                ),
            ),
        )

        # ⑥ The override is not persisted.
        resp = await _post(client, chat, dict(plain))
        answer = _answer_of(resp) or ""
        case(
            "not_persisted", "The override is not persisted",
            "An override written into the real skills directory works for one "
            "call and quietly changes the deployed agent for everyone else.",
            CheckResult(ok=True, detail="a later plain call was unaffected")
            if magic not in answer
            else CheckResult(
                ok=False,
                error=(
                    "a plain call afterwards still knew the overridden skill, so "
                    "the override outlived the request that carried it."
                ),
            ),
        )

        # ⑦ Path traversal is refused.
        hostile = build_payload(
            "x", uuid.uuid4().hex, "skill-studio-conformance", ["conformance"],
            budget, WorkspaceOverride(skills={"../../etc/passwd": "x"}),
        )
        resp = await _post(client, chat, hostile)
        case(
            "path_safety", "A traversing skill path is refused",
            "These keys are attacker-influenced strings that most "
            "implementations turn into filenames. Skill Studio never sends a "
            "hostile one, so nothing but this will exercise it.",
            CheckResult(ok=True, detail=f"refused with HTTP {resp.status_code}")
            if resp.status_code == 400
            else CheckResult(
                ok=False,
                error=(
                    f"expected HTTP 400, got {resp.status_code}. A path with "
                    "`..` in it must be rejected before anything is written."
                ),
            ),
        )

        # ⑨ Unknown keys are ignored, not rejected.
        forward_compatible = dict(plain)
        forward_compatible["skill_studio"] = {
            **forward_compatible["skill_studio"],
            "something_we_added_later": {"a": 1},
        }
        resp = await _post(client, chat, forward_compatible)
        answer = _answer_of(resp)
        case(
            "unknown_keys", "Unknown fields are ignored, not rejected",
            "Skill Studio adds fields. A server that rejects what it does not "
            "recognise breaks on the next release of this platform.",
            CheckResult(ok=True, detail="answered normally")
            if resp.status_code < 400 and (answer or "").strip()
            else CheckResult(
                ok=False,
                error=f"HTTP {resp.status_code}: {resp.text[:300]}",
            ),
        )

    # The skills endpoint, and what it reported.
    #
    # No fallback to the deployment's own URL, unlike everywhere else in this
    # platform. A blank field here means "my server has no skills endpoint", and
    # quietly testing a *different* agent instead would produce the one result
    # this whole page exists to prevent: a green mark that is about something
    # other than what was asked.
    why_skills = (
        "The playground, the skill-coverage warning and optimization all read "
        "this. Optional: without it, evaluation still runs."
    )
    if not (skills_url or "").strip():
        case(
            "skills", "Skills endpoint lists the files", why_skills,
            CheckResult(ok=None, detail="No skills endpoint given."),
        )
        return _finish(report)

    skills_result = await probe_skills(skills_url, settings.agent_probe_timeout_s)
    case("skills", "Skills endpoint lists the files", why_skills, skills_result.skills)
    if skills_result.skills.ok:
        derived = skills_result.version.startswith("sha256.")
        case(
            "version", "A version of the agent's own is reported",
            "It has to move when a model or prompt changes, not only when a "
            "skill file does — the staleness check and run comparability both "
            "rest on it.",
            CheckResult(ok=True, detail=f"version {skills_result.version}")
            if not derived
            else CheckResult(
                ok=None,
                detail=(
                    "no version reported, so one was derived from the skill "
                    "files. That fallback cannot see a model or prompt change."
                ),
            ),
        )

    return _finish(report)


def _finish(report: ConformanceReport) -> ConformanceReport:
    def ok(cid):
        return any(c.id == cid and c.result.ok is True for c in report.cases)

    if not ok("chat"):
        report.tier = 0
        report.summary = (
            "This agent cannot be used yet: the chat endpoint did not answer."
        )
    elif not ok("skills"):
        report.tier = 0
        report.summary = (
            "Evaluation will work against this agent. Add a skills endpoint to "
            "unlock the playground, the skill-coverage warning and optimization."
        )
    elif not ok("override"):
        report.tier = 1
        report.summary = (
            "Evaluation and the playground will work. Optimization needs the "
            "skills override to take effect."
        )
    else:
        report.tier = 2
        report.summary = (
            "Everything is available, including optimization — provided this "
            "agent also reuses the trace id it is given."
        )
    return report
