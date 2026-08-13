"""Preflight for the real integrations.

    docker compose run --rm backend python -m app.check_integrations

Pings whichever seams are set to `real` and reports each one OK or FAILED with
the reason. Getting a misconfigured endpoint back as one line here beats
discovering it three minutes into an eval run.
"""
from __future__ import annotations

import asyncio
import sys

import httpx

from app.config import settings

OK = "  OK   "
FAIL = " FAIL  "
SKIP = " SKIP  "


def _line(status: str, seam: str, detail: str) -> None:
    print(f"[{status}] {seam:<10} {detail}")


async def check_agent() -> bool:
    if settings.agent_impl != "real":
        _line(SKIP, "agent", "AGENT_IMPL=fake")
        return True
    if not settings.agent_base_url:
        _line(FAIL, "agent", "AGENT_BASE_URL is empty")
        return False
    url = f"{settings.agent_base_url.rstrip('/')}/execute"
    try:
        # A GET on a POST-only endpoint won't be a valid call, but it proves
        # the host resolves, TLS works and something is listening there. Any
        # HTTP status counts as reachable; only transport errors are failures.
        async with httpx.AsyncClient(
            timeout=settings.agent_timeout_s, follow_redirects=True
        ) as client:
            resp = await client.get(url)
        _line(OK, "agent", f"{url} reachable (HTTP {resp.status_code})")
        return True
    except Exception as exc:  # noqa: BLE001
        _line(FAIL, "agent", f"{url}: {exc}")
        return False


async def _check_llm(seam: str, model: str) -> bool:
    from app.integrations.real.llm import get_client

    if not model:
        _line(FAIL, seam, f"{seam.upper()}_MODEL is empty")
        return False
    try:
        completion = await get_client().chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with the single word: ok"}],
            temperature=0,
        )
        reply = (completion.choices[0].message.content or "").strip()[:40]
        _line(OK, seam, f"{model} replied: {reply!r}")
        return True
    except Exception as exc:  # noqa: BLE001
        _line(FAIL, seam, f"{model}: {exc}")
        return False


async def check_judge() -> bool:
    if settings.judge_impl != "real":
        _line(SKIP, "judge", "JUDGE_IMPL=fake")
        return True
    return await _check_llm("judge", settings.judge_model)


async def check_diagnosis() -> bool:
    if settings.diagnosis_impl != "real":
        _line(SKIP, "diagnosis", "DIAGNOSIS_IMPL=fake")
        return True
    return await _check_llm("diagnosis", settings.diagnosis_model)


async def check_trace() -> bool:
    if settings.trace_impl != "real":
        _line(SKIP, "trace", "TRACE_IMPL=fake")
        return True
    if not (settings.langfuse_host and settings.langfuse_public_key
            and settings.langfuse_secret_key):
        _line(FAIL, "trace", "LANGFUSE_HOST / _PUBLIC_KEY / _SECRET_KEY incomplete")
        return False
    host = settings.langfuse_host.rstrip("/")
    try:
        async with httpx.AsyncClient(
            timeout=settings.langfuse_timeout_s,
            auth=(settings.langfuse_public_key, settings.langfuse_secret_key),
        ) as client:
            # Querying a trace id that cannot exist: a 200 with an empty page
            # proves the host, the credentials and the API version are all good.
            resp = await client.get(
                f"{host}/api/public/v2/observations",
                params={"traceId": "preflight-nonexistent", "limit": 1},
            )
        if resp.status_code == 401:
            _line(FAIL, "trace", f"{host}: 401 — check the Langfuse key pair")
            return False
        resp.raise_for_status()
        _line(OK, "trace", f"{host} authenticated, observations API responding")
        return True
    except Exception as exc:  # noqa: BLE001
        _line(FAIL, "trace", f"{host}: {exc}")
        return False


async def check_workspace() -> bool:
    if settings.workspace_impl != "real":
        _line(SKIP, "workspace", "WORKSPACE_IMPL=fake")
        return True
    if not settings.agent_base_url:
        _line(
            FAIL, "workspace",
            "AGENT_BASE_URL is empty (the workspace lives on the agent server)",
        )
        return False
    from app.integrations.real.workspace import HttpWorkspaceClient

    try:
        # Unlike the agent check this is a real call: /get_workspace is a GET, so
        # there is no reason to settle for "something is listening".
        ws = await HttpWorkspaceClient().get_workspace()
    except Exception as exc:  # noqa: BLE001
        _line(FAIL, "workspace", f"{settings.agent_base_url}: {exc}")
        return False
    _line(
        OK, "workspace",
        f"version {ws.version or '(none)'}, {len(ws.skills)} skill file(s), "
        f"{len(ws.redacted_paths)} redacted config path(s)",
    )
    return True


async def main() -> int:
    print("Integration preflight")
    print(
        f"  modes: agent={settings.agent_impl} judge={settings.judge_impl} "
        f"trace={settings.trace_impl} diagnosis={settings.diagnosis_impl} "
        f"synthesis={settings.synthesis_impl} "
        f"workspace={settings.workspace_impl} "
        f"optimizer={settings.optimizer_impl}"
    )
    print()
    results = [
        await check_agent(),
        await check_judge(),
        await check_trace(),
        await check_diagnosis(),
        await check_workspace(),
    ]
    print()
    if all(results):
        print("All enabled integrations are reachable.")
        return 0
    print("One or more integrations failed — see FAIL lines above.")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
