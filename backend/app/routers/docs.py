"""Reference documentation, served from the repository's own markdown.

The agent-server contract exists as `backend/docs/agent-server-api.md` and is
what a developer is pointed at when they ask what their server has to do.
Putting a second copy in the UI would have been easier and would have gone
stale — and stale in the worst way, because the copy on screen is the one
somebody implements against while the file in the repository is the one that is
reviewed.

So the file is the only copy, and this hands it over verbatim. Changing the
contract is editing one markdown file; the UI follows without a rebuild.

**A whitelist, not a path.** `docs/` also holds internal notes and a full
platform spec, and the endpoint is reachable by anyone signed in. A name→file
map means a new document is a deliberate line here rather than a consequence of
where a file was saved, and it makes traversal unrepresentable rather than
merely blocked.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.auth import current_subject
from app.config import settings
from app.schemas import DocIndexEntryOut, DocOut, DocsIndexOut

router = APIRouter(prefix="/docs", tags=["docs"])


def _docs_dir() -> Path:
    """Where the markdown lives — one answer for both a checkout and the image.

    It used to be two, searched in order, because the documents sat at the
    repository root while the code sat under `backend/`: no single relative path
    reached them from both a `python -m pytest` checkout and a container whose
    WORKDIR is `/app`. Moving `docs/` into `backend/` collapsed that — the
    directory now sits beside `app/` in the checkout and is COPYed to the same
    place in the image, so `parents[2]` is `backend/` on a developer's machine
    and `/app` in the container, and `docs` hangs off it either way.

    `settings.docs_dir` still wins, for a deployment that mounts them elsewhere.
    """
    if settings.docs_dir:
        return Path(settings.docs_dir)
    # app/routers/docs.py -> app/routers -> app -> backend (or /app).
    return Path(__file__).resolve().parents[2] / "docs"


@dataclass(frozen=True)
class Published:
    """One document in the whitelist, plus where it sits in the navigation.

    `title` is the document's own — what the page is headed with. `nav_label` is
    what a sidebar row says, and the two are not the same job: "Agent Server
    API" heads the page, "API reference" is what you read under the topic that
    already says "Agent Server".
    """

    file: str
    title: str
    summary: str
    topic_id: str
    topic_label: str
    nav_label: str


# The documents the UI may ask for, by the name it uses in its own routes.
#
# **Order is the navigation's order**, topics included: the first entry of a
# topic is the page that topic opens on. Adding a document is a line here and
# nothing in the frontend — the sidebar is built from this, so a document cannot
# be published and stay invisible, and a sidebar row cannot point at a 404.
PUBLISHED = {
    "agent-server": Published(
        file="agent-server-api.md",
        title="Agent Server API",
        summary=(
            "What your agent server must expose to be evaluated, explored and "
            "optimised by Skill Studio."
        ),
        topic_id="agent-server",
        topic_label="Agent Server",
        nav_label="API reference",
    ),
}


# Before `/{name}`, which would otherwise match the empty path's sibling shapes
# first. Reading order here is matching order in FastAPI.
@router.get("", response_model=DocsIndexOut)
def list_docs(subject: str = Depends(current_subject)) -> DocsIndexOut:
    """What is published, in navigation order.

    No file is read: this answers "what is there", and a sidebar that had to
    read thirteen markdown files to draw itself would be paying a document's
    cost for a list of names.
    """
    return DocsIndexOut(
        docs=[
            DocIndexEntryOut(
                name=name,
                nav_label=entry.nav_label,
                title=entry.title,
                topic_id=entry.topic_id,
                topic_label=entry.topic_label,
            )
            for name, entry in PUBLISHED.items()
        ]
    )


@router.get("/{name}", response_model=DocOut)
def get_doc(name: str, subject: str = Depends(current_subject)):
    entry = PUBLISHED.get(name)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"no document named {name!r}")
    relative, title, summary = entry.file, entry.title, entry.summary
    path = _docs_dir() / relative
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
