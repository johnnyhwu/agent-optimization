"""Identity endpoints: who is signed in, and does this username exist.

`GET /users/lookup` is what makes sharing work once the fake user directory is
gone (§6.16). Sharing is a person typing a colleague's username into a box, and
the write it produces is `INSERT INTO eval_set_roles`. Nothing about that insert
can fail for a name that does not exist — the row is perfectly valid, it just
names an account that will never sign in — so without a lookup a typo produces an
eval set shared with nobody, and the person who typed it has no way to find out.

**A failed lookup and an unreachable directory are different answers.** Only the
first one blocks: "no such employee" is a fact about the input, and acting on it
prevents the silent failure above. "the directory did not answer" is a fact about
the directory, and blocking on it would mean an outage over there stops everyone
here from sharing anything. So the endpoint reports which of the two happened and
lets the UI warn-but-allow on the second.
"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import current_subject, normalize_subject
from app.config import settings

router = APIRouter(tags=["users"])


@router.get("/users")
async def users(subject: str = Depends(current_subject)):
    """The identity switcher's directory, plus who the caller currently is.

    In keycloak mode there is no switching — identity comes from the token — so
    the list is empty and the frontend hides the control. The endpoint stays
    because `current` is still worth having, and because a mode-dependent shape
    is cheaper than a mode-dependent set of endpoints.
    """
    if settings.auth_mode == "fake":
        return {"users": settings.known_users, "current": subject}
    return {"users": [], "current": subject}


@router.get("/users/lookup")
async def lookup_user(
    username: str = Query(..., min_length=1),
    subject: str = Depends(current_subject),
):
    """Resolve a username against the employee directory before it is shared with.

    Returns `{username, employee_name, verified}`. `verified=False` means the
    directory could not be reached and the name is therefore unconfirmed — not
    that it is wrong. A username the directory positively denies is a 404.
    """
    normalized = normalize_subject(username)
    if not normalized:
        raise HTTPException(status_code=404, detail="no such user")

    if settings.auth_mode == "fake":
        # Local development and the seeded demo have no directory to call, and
        # requiring one would make the share dialog unusable offline.
        if normalized not in {normalize_subject(u) for u in settings.known_users}:
            raise HTTPException(status_code=404, detail=f"no such user: {username}")
        return {"username": normalized, "employee_name": normalized, "verified": True}

    url = f"{settings.hr_api_base_url.rstrip('/')}/{normalized}"
    try:
        # httpx, not requests: this call sits behind a keystroke in the share
        # dialog, and a synchronous client would block the event loop for its
        # whole duration — freezing every other request in the process,
        # including the SSE streams carrying run progress.
        async with httpx.AsyncClient(
            timeout=settings.hr_api_timeout_s, verify=settings.hr_api_verify_ssl
        ) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        return {
            "username": normalized,
            "employee_name": None,
            "verified": False,
            "reason": f"could not reach the employee directory: {exc}",
        }

    if response.status_code >= 400:
        raise HTTPException(status_code=404, detail=f"no such user: {username}")

    try:
        body = response.json()
    except ValueError:
        return {
            "username": normalized,
            "employee_name": None,
            "verified": False,
            "reason": "the employee directory did not return JSON",
        }

    # The directory answers a hit with employee_id/employee_name and a miss with
    # a lone `detail`. Some deployments serve the miss as 200, so the body shape
    # is checked rather than trusted to the status code.
    if not isinstance(body, dict) or not body.get("employee_name"):
        raise HTTPException(status_code=404, detail=f"no such user: {username}")

    return {
        "username": normalized,
        "employee_name": str(body["employee_name"]),
        "verified": True,
    }
