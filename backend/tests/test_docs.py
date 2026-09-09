"""The reference docs endpoint: one copy of the contract, served from the file.

The thing worth testing here is not that a file can be read. It is that the file
the UI serves is the file the repository reviews — a second copy would go stale
in the direction that matters most, since the on-screen one is what somebody
implements against.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.routers import docs as docs_router


def test_the_contract_is_served_from_the_repository_file():
    out = docs_router.get_doc("agent-server", subject="alice")

    assert out.title
    # Not an assertion about wording — an assertion that this is the real file
    # and not a placeholder that would render as a page with nothing on it.
    assert "chat endpoint" in out.markdown.lower()
    assert len(out.markdown) > 2000


def test_the_served_text_is_byte_for_byte_the_file():
    from pathlib import Path

    backend = Path(docs_router.__file__).resolve().parents[2]
    on_disk = (backend / "docs/agent-server-api.md").read_text("utf-8")

    assert docs_router.get_doc("agent-server", subject="alice").markdown == on_disk


def test_an_unlisted_document_is_a_404_not_a_read():
    """A whitelist, not a path.

    `docs/` also holds internal notes and the full platform spec, and this
    endpoint is reachable by anyone signed in. Nothing outside the map is
    readable, so there is no traversal to block — the shape of the code makes it
    unrepresentable.
    """
    with pytest.raises(HTTPException) as caught:
        docs_router.get_doc("spec", subject="alice")
    assert caught.value.status_code == 404


@pytest.mark.parametrize(
    "name",
    ["../spec", "../../etc/passwd", "agent-server/../spec", "/etc/passwd"],
)
def test_traversal_shapes_are_simply_not_in_the_map(name):
    with pytest.raises(HTTPException) as caught:
        docs_router.get_doc(name, subject="alice")
    assert caught.value.status_code == 404


def test_the_docs_directory_can_be_pointed_elsewhere(configure, tmp_path):
    """One path now, plus an override.

    `parents[2]` is `backend/` in a checkout and `/app` in the image, because the
    documents live inside the backend directory and are COPYed to the same place
    beside `app/`. That is why there is no longer a list of candidates to search:
    the two cases have the same answer. `docs_dir` remains for a deployment that
    mounts them somewhere else entirely.
    """
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "agent-server-api.md").write_text("# Moved\n", "utf-8")

    with configure(docs_dir=str(elsewhere)):
        assert docs_router.get_doc("agent-server", subject="alice").markdown == "# Moved\n"

    # Unset, it finds the copy beside the code without being told.
    assert "chat endpoint" in (
        docs_router.get_doc("agent-server", subject="alice").markdown.lower()
    )


def test_every_published_document_actually_exists():
    """A whitelist that names a missing file is a 500 nobody finds until a user
    clicks a link. Cheaper to notice here."""
    for name in docs_router.PUBLISHED:
        assert docs_router.get_doc(name, subject="alice").markdown
