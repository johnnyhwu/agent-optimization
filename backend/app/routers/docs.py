"""Reference documentation, served from the repository's own markdown.

The agent-server contract exists as `docs/agent-server-api.md` and is what a
developer is pointed at when they ask what their server has to do. Putting a
second copy in the UI would have been easier and would have gone stale — and
stale in the worst way, because the copy on screen is the one somebody
implements against while the file in the repository is the one that is reviewed.

So the file is the only copy, and this hands it over verbatim. Changing the
contract is editing one markdown file; the UI follows without a rebuild.

**A whitelist, not a path.** `docs/` also holds internal notes and a full
platform spec, and the endpoint is reachable by anyone signed in. A name→file
map means a new document is a deliberate line here rather than a consequence of
where a file was saved, and it makes traversal unrepresentable rather than
merely blocked.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.auth import current_subject
from app.schemas import DocOut

router = APIRouter(prefix="/docs", tags=["docs"])

# `app/routers/docs.py` -> repository root.
_REPO_ROOT = Path(__file__).resolve().parents[3]

# The documents the UI may ask for, by the name it uses in its own routes.
PUBLISHED = {
    "agent-server": (
        "docs/agent-server-api.md",
        "Agent Server API",
        "What your agent server must expose to be evaluated, explored and "
        "optimised by Skill Studio.",
    ),
}


@router.get("/{name}", response_model=DocOut)
def get_doc(name: str, subject: str = Depends(current_subject)):
    entry = PUBLISHED.get(name)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"no document named {name!r}")
    relative, title, summary = entry
    path = _REPO_ROOT / relative
    try:
        text = path.read_text("utf-8")
    except OSError as exc:
        # A deployment that did not ship the docs directory is a packaging
        # problem, and saying so beats an empty page that reads as a document
        # with nothing in it.
        raise HTTPException(
            status_code=500, detail=f"could not read {relative}: {exc}"
        ) from exc
    return DocOut(name=name, title=title, summary=summary, markdown=text)
