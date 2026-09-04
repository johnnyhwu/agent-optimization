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

import time

import httpx

from app.config import settings
from app.integrations.base import WorkspaceOverride
from app.integrations.real.agent import build_payload
from app.integrations.real.agent_auth import (
    auth_headers,
    credentialed_client,
    same_origin,
)
from app.integrations.real.workspace import HttpWorkspaceClient
from app.services.agent_probe import (
    CheckResult,
    credential_state,
    make_probe_skill,
    with_auth_hint,
)


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


# How long the whole checklist may take, however slow the agent is. Six model
# calls at the per-call timeout is 12 minutes at the default, and nginx gives up
# on a request at 660s (`frontend/nginx.conf.template`) — so without a cap the
# checklist's worst case is a 504 with no report in it, which is the one outcome
# worse than a slow one. Under that limit on purpose, so what the developer sees
# is this file's own sentence rather than the proxy's.
CHECKLIST_BUDGET_S = 540.0


class _OutOfTime(Exception):
    """The total budget is spent, so the checklist stops where it is.

    Raised rather than returned so it unwinds to one place. What has been
    established so far is still worth showing: the cases already recorded are
    kept, and the report says why it stops there.
    """


async def _post(
    client: httpx.AsyncClient,
    url: str,
    body: dict,
    headers: dict[str, str] | None = None,
    *,
    deadline: float | None = None,
) -> httpx.Response:
    if deadline is not None:
        left = deadline - time.monotonic()
        if left <= 0:
            # The sentence is written where the budget is known — here there
            # is only a deadline, not the number it came from.
            raise _OutOfTime()
        return await client.post(
            url,
            json=body,
            headers={"Content-Type": "application/json", **(headers or {})},
            timeout=left,
        )
    return await client.post(
        url, json=body, headers={"Content-Type": "application/json", **(headers or {})}
    )


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
    chat_url: str,
    skills_url: str = "",
    timeout_s: float | None = None,
    *,
    api_key: str = "",
    auth_header: str = "",
    budget_s: float | None = None,
) -> ConformanceReport:
    """The whole checklist against one server.

    The credential is optional and, unlike every other input here, **is not
    itself checked**. There is no case that a server without authentication can
    fail: asking for no credential is not a defect, and this platform has no
    standing to grade it. The key exists so that a developer whose server is
    behind a gateway can run the checklist at all.

    **Always returns a report.** Six live model calls is six chances for the
    server to stop answering, and losing the whole page to the seventh minute of
    a slow one taught nobody anything. A run that cannot continue keeps what it
    established and says where it stopped.
    """
    report = ConformanceReport()
    total = budget_s or CHECKLIST_BUDGET_S
    deadline = time.monotonic() + total
    try:
        return await _run_checklist(
            report, chat_url, skills_url, timeout_s,
            api_key=api_key, auth_header=auth_header, deadline=deadline,
        )
    except _OutOfTime:
        _abort(
            report,
            f"the checklist ran out of time after {total:.0f}s. The cases above "
            "are what it got through; a server this slow to answer will also be "
            "slow to evaluate.",
        )
        return _finish(report)
    except httpx.HTTPError as exc:
        # Case ① has its own handler for an unreachable server, so anything
        # reaching here failed *after* the endpoint had answered once — a
        # timeout, a dropped connection, a gateway that gave up. That is the
        # agent's behaviour under load, and it is worth reporting as such rather
        # than as a 500 with no report at all.
        _abort(
            report,
            f"the agent server stopped answering partway through the checklist: "
            f"{type(exc).__name__}: {exc}",
        )
        return _finish(report)


def _abort(report: ConformanceReport, reason: str) -> None:
    report.cases.append(
        ConformanceCase(
            id="checklist",
            title="The checklist finished",
            why=(
                "Every case below this line was skipped, so this report is "
                "partial — a case that did not run is not a case that passed."
            ),
            result=CheckResult(ok=False, error=reason),
        )
    )


async def _run_checklist(
    report: ConformanceReport,
    chat_url: str,
    skills_url: str = "",
    timeout_s: float | None = None,
    *,
    api_key: str = "",
    auth_header: str = "",
    deadline: float | None = None,
) -> ConformanceReport:
    chat = (chat_url or "").strip()
    budget = timeout_s or settings.agent_timeout_s
    headers = auth_headers(api_key, auth_header)

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

    async with credentialed_client(
        chat, timeout_s=budget, api_key=api_key, auth_header=auth_header
    ) as client:
        # ① A plain call: the baseline everything else varies from.
        plain = build_payload(
            "Reply with the single word: ok", uuid.uuid4().hex,
            "skill-studio-conformance", ["conformance"], budget, None,
        )
        try:
            resp = await _post(client, chat, plain, headers, deadline=deadline)
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
                    # The same hint the connection probes give. This page is the
                    # one a developer whose server sits behind a gateway is most
                    # likely to reach first, and a bare "HTTP 401" here is the
                    # one place the advice is worth the most.
                    error=with_auth_hint(
                        f"HTTP {resp.status_code}: {resp.text[:400]}",
                        credential=credential_state(api_key),
                    ),
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
        resp = await _post(client, chat, with_override, headers, deadline=deadline)
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
        resp = await _post(client, chat, empty, headers, deadline=deadline)
        answer = _answer_of(resp) or ""
        # What this can prove, and what it cannot, stated rather than implied.
        # An agent that reads `{}` as "use your own" answers from its deployed
        # files — which do not contain the value we invented either, so the
        # reply looks identical to a correct one. Distinguishing the two needs a
        # question only the deployed files can answer, and the platform has no
        # way to write one. So this reports the half it can see, and the `why`
        # says the other half is yours to check.
        case(
            "empty_skills", "An empty skills map is accepted and clears the override",
            "`{}` means \"run with no skills\", and `{}` is falsy in every "
            "language this gets written in — so `if skills:` silently turns it "
            "into \"use your own\". This case catches an empty map that leaks "
            "the previous call's files; it cannot see a fallback to your "
            "deployed ones, because that answer looks correct. Test that half "
            "with a question only your own skills can answer.",
            CheckResult(ok=True, detail="answered without the sent skill")
            if resp.status_code < 400 and magic not in answer
            else CheckResult(
                ok=False,
                error=(
                    f"HTTP {resp.status_code}: {resp.text[:300]}"
                    if resp.status_code >= 400
                    else "the answer still contained the previous call's skill "
                    "value, so an empty map did not clear the skills for this call."
                ),
            ),
        )

        # ⑥ The override is not persisted.
        #
        # The *magic* question with no skills key at all — "use your own files".
        # Re-sending the plain question instead proved nothing: no agent answers
        # "reply with the single word: ok" by quoting a skill, so the check
        # passed whether or not the override had been written to disk.
        persisted_probe = build_payload(
            question, uuid.uuid4().hex, "skill-studio-conformance",
            ["conformance"], budget, None,
        )
        resp = await _post(client, chat, persisted_probe, headers, deadline=deadline)
        answer = _answer_of(resp) or ""
        case(
            "not_persisted", "The override is not persisted",
            "An override written into the real skills directory works for one "
            "call and quietly changes the deployed agent for everyone else.",
            CheckResult(ok=True, detail="a later call with no override was unaffected")
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
        resp = await _post(client, chat, hostile, headers, deadline=deadline)
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
        resp = await _post(client, chat, forward_compatible, headers, deadline=deadline)
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

    # Straight to the HTTP client, not through `build_seams`. Everywhere else in
    # the platform the seam is right: a deployment on fake seams should probe
    # the fake ones. Here it would be a lie — this page exists to report on the
    # server whose URL was just typed, and answering about a canned workspace
    # instead is the one result it must never produce.
    client = HttpWorkspaceClient(
        skills_url=skills_url,
        timeout_s=settings.agent_probe_timeout_s,
        # Same rule as everywhere else: the chat endpoint's credential travels
        # to the skills endpoint only when they are the same server.
        **(
            {"api_key": api_key, "auth_header": auth_header}
            if api_key and same_origin(chat, skills_url)
            else {}
        ),
    )
    try:
        workspace = await client.get_workspace()
    except Exception as exc:  # noqa: BLE001 - the agent server's problem, quoted as-is
        case(
            "skills", "Skills endpoint lists the files", why_skills,
            CheckResult(
                ok=False,
                error=with_auth_hint(
                    str(exc) or type(exc).__name__,
                    # Only a key that actually reached this endpoint counts as
                    # sent: one withheld by the same-origin rule gets a sentence
                    # of its own, naming the withholding as the reason.
                    credential=credential_state(
                        api_key, chat_url=chat, target_url=skills_url
                    ),
                ),
            ),
        )
        return _finish(report)

    n = len(workspace.skills)
    case(
        "skills", "Skills endpoint lists the files", why_skills,
        CheckResult(ok=True, detail=f"{n} skill file{'' if n == 1 else 's'}"),
    )
    # `derived_version` prefixes what it computes, so this distinguishes "the
    # agent told us" from "we hashed the files ourselves" — which is the whole
    # difference between a staleness check and half of one.
    derived = workspace.version.startswith("sha256.")
    case(
        "version", "A version of the agent's own is reported",
        "It has to move when a model or prompt changes, not only when a skill "
        "file does — the staleness check and run comparability both rest on it.",
        CheckResult(ok=True, detail=f"version {workspace.version}")
        if not derived
        else CheckResult(
            ok=None,
            detail=(
                "no version reported, so one was derived from the skill files. "
                "That fallback cannot see a model or prompt change."
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
