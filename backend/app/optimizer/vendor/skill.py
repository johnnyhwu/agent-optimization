"""ReflACT skill operations — edit application and patch processing.

The Update stage (⑤) of the ReflACT pipeline: apply a ranked set of
edits to the current skill document, producing an updated candidate.
Analogous to optimizer.step() in neural network training.

--- FORK NOTE (see ../VENDORED.md) ------------------------------------------
Upstream operates on **one string**. Here a skill is a **directory**, so this
module operates on a ``{relative path: text}`` mapping and every edit carries a
``path``. Three things follow, and nothing else about the algorithm changes:

  1. ``append`` concatenates at the end of ``files[path]``, not at the end of
     "the document". A path that does not exist yet is created.
  2. ``path`` is model output that ends up as a key in the workspace override
     sent to the agent server, so it is validated against the skill directory
     before anything is written.
  3. Upstream's module-level ``_PROTECTED_REGIONS`` becomes a ``Protection``
     parameter. The two marker regions it named are still protected exactly as
     before; the parameter adds the mode-dependent half (isolated protects the
     frontmatter, routing protects the body), which upstream has no concept of
     because upstream has no routing mode.

The append anchor deliberately still considers **only the marker regions**, as
upstream does: those sit at the document tail and an append must land before
them. The frontmatter is a head region and is a veto, never an anchor.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Mapping, Sequence

if TYPE_CHECKING:  # pragma: no cover - typing only
    from skillopt.types import Edit as EditType, Patch as PatchType

SLOW_UPDATE_START = "<!-- SLOW_UPDATE_START -->"
SLOW_UPDATE_END = "<!-- SLOW_UPDATE_END -->"

# Skill-aware reflection (EmbodiSkill S_app) appendix region. Like the slow
# update region, it is protected: step-level analyst edits must not modify it.
APPENDIX_START = "<!-- APPENDIX_START -->"
APPENDIX_END = "<!-- APPENDIX_END -->"

# All protected (start, end) marker pairs. Step-level edits cannot target text
# inside any of these regions, and `append` / `insert_after`-fallback ops are
# inserted before the earliest-occurring region so protected blocks stay at the
# document tail.
_PROTECTED_REGIONS: tuple[tuple[str, str], ...] = (
    (SLOW_UPDATE_START, SLOW_UPDATE_END),
    (APPENDIX_START, APPENDIX_END),
)

_ENTRY_POINT_NAME = "SKILL.md"


@dataclass(frozen=True)
class Protection:
    """What step-level edits may not touch, for one optimization mode.

    Replaces upstream's module-level `_PROTECTED_REGIONS` constant. The marker
    regions above are protected unconditionally and are not described here; this
    only carries the parts that depend on the mode.

    `protect` is resolved against the entry points only:
      * ``"frontmatter"`` — isolated mode. The body is optimised; the
        description cannot be validated by the gate when only one skill is sent,
        so it is frozen.
      * ``"body"`` — routing mode. The description is optimised; the body is
        frozen so a routing run cannot quietly become a body-optimising one.
      * ``"none"`` — no mode-dependent protection (upstream's behaviour).

    `entry_points` is a set because a routing run optimises the descriptions of
    several skills together — they compete, so widening one narrows the others
    by implication, and a run allowed to move only one boundary would be scored
    against a workspace half of which was frozen against it. What does not
    follow is a wider licence: each target gives up its own description and
    nothing else.
    """

    entry_points: frozenset[str] = field(default_factory=frozenset)
    protect: str = "none"
    readonly: frozenset[str] = field(default_factory=frozenset)
    allow_append: bool = True

    @property
    def entry_point(self) -> str | None:
        """The single entry point, for the callers that can only have one.

        `None` when there are several: "the skill document" has no referent
        then, and every use of this is a place that must say which one.
        """
        return next(iter(self.entry_points)) if len(self.entry_points) == 1 else None


def _frontmatter_span(text: str) -> tuple[int, int] | None:
    """`(start, end)` of the leading YAML frontmatter block, or None.

    The single implementation: `app.optimizer.skillio.frontmatter_span`
    delegates here, so the dependency runs ours -> vendored and the fork stays a
    leaf.

    Scanned line by line rather than matched against `"---\\n"`, because that
    literal recognises only one *encoding* of a file authors write identically,
    and each miss is silent. A `SKILL.md` checked out with `core.autocrlf` opens
    `---\\r\\n`; one saved by an editor that writes a BOM opens `\\ufeff---`.
    Both have a description, both used to report having none — which marks the
    skill unavailable for routing, freezes the whole file against routing edits
    (`_mode_spans`), and lets the detector count a menu listing the description
    as proof the skill was *loaded*.

    Only differences the author cannot see are tolerated. A blank line **above**
    the opening delimiter is not one of them: the first line is then empty
    rather than `---`, which is a different document, and every frontmatter
    parser this platform's skills are written against reads it that way too. So
    the rules are:

      * a delimiter is a line whose stripped text is exactly ``---``, so CRLF
        and trailing whitespace are the same delimiter as a bare one;
      * a leading BOM is skipped — it is a byte, not a line;
      * the block must **open the file**. Anything above it, blank or prose,
        makes a later ``---`` a horizontal rule; an opening delimiter with
        nothing closing it is a rule or a truncated file. Both answer None,
        because reading either as frontmatter would hand routing mode a licence
        to edit the body.

    Offsets are returned into the *original* string: callers slice `text` with
    them (`_mode_spans`, `detector._markers`), so any skipping done here must
    not shift what the numbers mean.
    """
    if not text:
        return None

    lines = text.split("\n")

    # The opening delimiter is the first line, allowing for a BOM in front of it.
    if lines[0].lstrip("﻿").rstrip() != "---":
        return None

    for index in range(1, len(lines)):
        if lines[index].rstrip() != "---":
            continue
        # Character offset of the end of this line, plus its newline when the
        # file has one there. `+ 1` per line consumed is the separator `split`
        # removed; the final `+ 1` is the newline after the closing delimiter,
        # which a file ending at the delimiter does not have.
        end = sum(len(lines[i]) + 1 for i in range(index)) + len(lines[index])
        return (0, end + 1 if index + 1 < len(lines) else end)

    # Opened and never closed: a horizontal rule, or a file cut short.
    return None


def _marker_spans(text: str) -> list[tuple[int, int]]:
    """Byte ranges of the upstream protected marker regions, recomputed live."""
    spans: list[tuple[int, int]] = []
    for start_marker, end_marker in _PROTECTED_REGIONS:
        start_idx = text.find(start_marker)
        end_idx = text.find(end_marker)
        if start_idx == -1 or end_idx == -1:
            continue
        spans.append((start_idx, end_idx + len(end_marker)))
    return spans


def _mode_spans(text: str, path: str, protection: Protection) -> list[tuple[int, int]]:
    """The mode-dependent protected ranges. Empty for every non-entry-point file."""
    if path not in protection.entry_points or protection.protect == "none":
        return []
    front = _frontmatter_span(text)
    if protection.protect == "frontmatter":
        return [front] if front else []
    if protection.protect == "body":
        # No frontmatter means routing mode has nothing to optimise here, so the
        # whole file is frozen. Falling back to "everything is editable" would
        # turn a routing run into a body-optimising run gated on the routing
        # guard — the exact confusion the two modes exist to prevent.
        return [(front[1], len(text))] if front else [(0, len(text))]
    return []


def _protected_spans(text: str, path: str, protection: Protection) -> list[tuple[int, int]]:
    return _marker_spans(text) + _mode_spans(text, path, protection)


def _earliest_protected_start(text: str) -> int:
    """Index of the earliest **marker** region start, or -1 if none.

    Upstream's helper, unchanged in meaning: it is the append anchor, and only
    the tail marker regions may act as one.
    """
    positions = [start for start, _ in _marker_spans(text)]
    return min(positions) if positions else -1


def _is_in_protected_region(
    text: str, target: str, path: str = "", protection: Protection | None = None
) -> bool:
    """Check if *target* text falls within any protected region."""
    if not target:
        return False
    target_idx = text.find(target)
    if target_idx == -1:
        return False
    spans = (
        _protected_spans(text, path, protection)
        if protection is not None
        else _marker_spans(text)
    )
    target_end = target_idx + len(target)
    return any(start < target_end and target_idx < end for start, end in spans)


def _strip_slow_update_markers(text: str) -> str:
    """Remove any protected-region markers from edit content to prevent duplication."""
    return (
        text.replace(SLOW_UPDATE_START, "")
            .replace(SLOW_UPDATE_END, "")
            .replace(APPENDIX_START, "")
            .replace(APPENDIX_END, "")
    )


def _field(edit: EditType | dict, name: str, default=None):
    if hasattr(edit, name):
        return getattr(edit, name)
    if isinstance(edit, dict):
        return edit.get(name, default)
    return default


def _edit_fields(edit: EditType | dict) -> tuple[str, str, str]:
    op = _field(edit, "op", "") or ""
    content = _strip_slow_update_markers((_field(edit, "content", "") or "").strip())
    target = _field(edit, "target", "") or ""
    return op, content, target


def _normalise_path(raw: str, skill_dir: str | Sequence[str]) -> str | None:
    """The path this edit may write to, or None if it escapes every skill.

    `skill_dir` is one directory or several: a routing run optimises a set of
    skills together, and an edit is in bounds when it names a file inside any
    one of them. It is still bounded — a path outside all of them is refused
    exactly as before, which is what keeps LLM output from authoring a write
    outside the directories this run was given.

    Kept here rather than imported from `app.optimizer.skillio` so the vendored
    package has no dependency on ours; `skillio.validate_skill_path` delegates
    to this one, so there is a single implementation.
    """
    import posixpath

    if not isinstance(raw, str):
        return None
    candidate = raw.strip().replace("\\", "/")
    if not candidate or candidate.startswith("/"):
        return None
    # A Windows drive letter is not a relative path, whatever normpath makes of it.
    if len(candidate) > 1 and candidate[1] == ":":
        return None
    normalised = posixpath.normpath(candidate)
    if normalised in (".", "..") or normalised.startswith("../"):
        return None
    dirs = [skill_dir] if isinstance(skill_dir, str) else list(skill_dir)
    for directory in dirs:
        prefix = f"{directory}/"
        if normalised.startswith(prefix) and normalised != prefix:
            return normalised
    return None


def _apply_edit_with_report(
    files: dict[str, str],
    edit: EditType | dict,
    *,
    skill_dir: str,
    protection: Protection,
) -> tuple[dict[str, str], dict]:
    op, content, target = _edit_fields(edit)
    raw_path = _field(edit, "path", None)
    # An edit with no `path` at all is an upstream-shaped edit: it means "the
    # skill document", which here is the entry point. An edit with a *blank*
    # path is a malformed one and is rejected — the two must not collapse into
    # the same case, or a typo becomes a silent write to SKILL.md.
    #
    # With several targets there is no "the skill document" to default to, and
    # guessing one would write one skill's description into another. The edit is
    # refused instead: `entry_point` is None there, which falls through to the
    # invalid-path branch below.
    defaulted = raw_path is None
    path = protection.entry_point if defaulted else _normalise_path(raw_path, skill_dir)

    report = {
        "op": op,
        "path": path or (raw_path if isinstance(raw_path, str) else ""),
        "path_defaulted": defaulted,
        "target": target[:200],
        "content_preview": content[:200],
        "status": "unknown",
    }

    if path is None:
        report["status"] = "skipped_invalid_path"
        return files, report

    if path in protection.readonly:
        report["status"] = "skipped_readonly_file"
        return files, report

    if op == "append" and not protection.allow_append:
        report["status"] = "skipped_append_not_allowed"
        return files, report

    text = files.get(path, "")
    created = path not in files

    if target and _is_in_protected_region(text, target, path, protection):
        report["status"] = "skipped_protected_region"
        return files, report

    def written(new_text: str, status: str) -> tuple[dict[str, str], dict]:
        # An emptied entry point is a deleted skill by another name: the agent
        # would be sent a skill with no instructions and every later step would
        # optimise a blank file.
        if path in protection.entry_points and not new_text.strip():
            report["status"] = "skipped_would_empty_entry_point"
            return files, report
        report["status"] = status
        return {**files, path: new_text}, report

    if op == "append":
        # Routing mode never reaches here (rejected above), so an append always
        # means "add to the body", and the anchor rule is upstream's.
        if created:
            return written(content + "\n", "applied_append_created_file")
        prot_start = _earliest_protected_start(text)
        if prot_start != -1:
            before = text[:prot_start].rstrip()
            after = text[prot_start:]
            return written(
                before + "\n\n" + content + "\n\n" + after,
                "applied_append_before_protected_region",
            )
        return written(text.rstrip() + "\n\n" + content + "\n", "applied_append")

    if op == "insert_after":
        if not target or target not in text:
            if created:
                return written(content + "\n", "applied_insert_after_created_file")
            prot_start = _earliest_protected_start(text)
            if prot_start != -1:
                before = text[:prot_start].rstrip()
                after = text[prot_start:]
                return written(
                    before + "\n\n" + content + "\n\n" + after,
                    "applied_insert_after_fallback_before_protected_region",
                )
            return written(
                text.rstrip() + "\n\n" + content + "\n",
                "applied_insert_after_fallback_append",
            )
        idx = text.index(target) + len(target)
        newline = text.find("\n", idx)
        insert_at = newline + 1 if newline != -1 else len(text)
        return written(
            text[:insert_at] + "\n" + content + "\n" + text[insert_at:],
            "applied_insert_after",
        )

    if op == "replace":
        if not target:
            report["status"] = "skipped_replace_missing_target"
            return files, report
        if target not in text:
            report["status"] = "skipped_replace_target_not_found"
            return files, report
        return written(text.replace(target, content, 1), "applied_replace")

    if op == "delete":
        if not target:
            report["status"] = "skipped_delete_missing_target"
            return files, report
        if target not in text:
            report["status"] = "skipped_delete_target_not_found"
            return files, report
        return written(text.replace(target, "", 1), "applied_delete")

    report["status"] = "skipped_unknown_op"
    return files, report


def apply_edit(
    files: Mapping[str, str],
    edit: EditType | dict,
    *,
    skill_dir: str,
    protection: Protection,
) -> dict[str, str]:
    """Apply a single edit operation to the skill directory."""
    updated, _ = _apply_edit_with_report(
        dict(files), edit, skill_dir=skill_dir, protection=protection
    )
    return updated


def apply_patch_with_report(
    files: Mapping[str, str],
    patch: PatchType | dict,
    *,
    skill_dir: str,
    protection: Protection,
) -> tuple[dict[str, str], list[dict]]:
    """Apply a patch and return a per-edit report for observability.

    The report is not diagnostics — it is what the Part 2 page renders to say
    which proposed edits actually reached the skill. "The model's idea was bad"
    and "the model's target string had a typo" are different problems and only
    this distinguishes them.
    """
    edits = patch.edits if hasattr(patch, "edits") else patch.get("edits", [])
    current = dict(files)
    reports: list[dict] = []
    for idx, edit in enumerate(edits, 1):
        try:
            current, report = _apply_edit_with_report(
                current, edit, skill_dir=skill_dir, protection=protection
            )
            report["index"] = idx
        except Exception as exc:  # noqa: BLE001 - one bad edit must not end a run
            report = {
                "index": idx,
                "op": "",
                "path": "",
                "path_defaulted": False,
                "target": "",
                "content_preview": "",
                "status": "error",
                "error": str(exc),
            }
        reports.append(report)
    return current, reports


def apply_patch(
    files: Mapping[str, str],
    patch: PatchType | dict,
    *,
    skill_dir: str,
    protection: Protection,
) -> dict[str, str]:
    """Apply a patch (list of edits) to the skill directory sequentially."""
    updated, _ = apply_patch_with_report(
        files, patch, skill_dir=skill_dir, protection=protection
    )
    return updated


def entry_point_for(skill_dir: str) -> str:
    """The file a skill is entered through. Never deletable, never emptied."""
    return f"{skill_dir}/{_ENTRY_POINT_NAME}"
