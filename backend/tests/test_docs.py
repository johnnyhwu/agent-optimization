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


# ---- the index the sidebar is built from -----------------------------------
#
# The navigation is derived from `PUBLISHED` rather than written out in the
# frontend, for the same reason the markdown is served rather than retyped: a
# second list would drift, and it would drift silently — a document published
# here and missing from a hand-written sidebar is simply invisible.


def test_the_index_lists_every_published_document_and_nothing_else():
    out = docs_router.list_docs(subject="alice")

    assert [d.name for d in out.docs] == list(docs_router.PUBLISHED)


def test_the_index_is_in_declaration_order():
    """Order is the navigation's order, so it is part of the contract.

    The first entry of a topic is the page that topic opens on. A dict that
    reordered itself would silently change which page a topic lands you on.
    """
    out = docs_router.list_docs(subject="alice")
    names = [d.name for d in out.docs]

    assert names == sorted(names, key=list(docs_router.PUBLISHED).index)


def test_nothing_in_the_index_is_a_dead_link():
    """A sidebar row that 404s is worse than a missing row: it reads as a
    broken deployment rather than as a document that does not exist."""
    for entry in docs_router.list_docs(subject="alice").docs:
        assert docs_router.get_doc(entry.name, subject="alice").markdown


def test_the_index_does_not_leak_unpublished_documents():
    """`docs/` also holds internal notes and the full platform spec. The index
    is reachable by anyone signed in, so it must not advertise them."""
    names = {d.name for d in docs_router.list_docs(subject="alice").docs}

    assert "spec" not in names
    assert "ui-redesign-plan" not in names


def test_every_index_entry_carries_a_topic_and_a_short_label():
    """Both are what the sidebar draws. A blank one renders as a row with no
    text — a gap in the navigation that nothing reports."""
    for entry in docs_router.list_docs(subject="alice").docs:
        assert entry.topic_id
        assert entry.topic_label
        assert entry.nav_label
