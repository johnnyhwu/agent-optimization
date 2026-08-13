"""Reading, comparing and packaging a skill *directory*.

Upstream SkillOpt's parameter is one markdown document; ours is a directory, and
everything that follows from that difference lives here rather than in
`vendor/`: how the two modes decide what may be edited, how two snapshots are
compared, how a snapshot becomes a download, and how a memorised gold answer is
spotted before a human has to notice it.

**Line counts are computed once, here, and displayed verbatim.** The `+5 / −10`
beside a file in the diff tree, the totals on a step row and the numbers in the
chart tooltip all read the same `per_file_stats` output. The browser renders its
own side-by-side diff for the rows, but it never recounts — two implementations
of "how many lines changed" would eventually disagree, on screen, about the same
edit.
"""
from __future__ import annotations

import difflib
import io
import json
import zipfile
from typing import Iterable, Mapping

from app.optimizer.vendor.skill import (
    Protection,
    _frontmatter_span,
    _normalise_path,
    entry_point_for,
)

# A gold answer shorter than this is not evidence of memorisation — "42" or
# "Yes" will appear in a well-written skill by coincidence, and a warning that
# fires on coincidence is ignored within a day.
MIN_LEAK_CHARS = 4


def validate_skill_path(path: str, skill_dir: str) -> str | None:
    """The normalised path an edit may write to, or None if it escapes the skill."""
    return _normalise_path(path, skill_dir)


def frontmatter_span(text: str) -> tuple[int, int] | None:
    """`(start, end)` of the leading YAML frontmatter block, or None."""
    return _frontmatter_span(text)


def has_frontmatter(files: Mapping[str, str], skill_dir: str) -> bool:
    """Whether this skill has a description block for routing mode to optimise."""
    return frontmatter_span(files.get(entry_point_for(skill_dir), "")) is not None


def build_protection(
    files: Mapping[str, str], skill_dir: str, mode: str
) -> Protection:
    """What step-level edits may not touch, for one optimization mode.

    The two modes are mirror images, which is the whole reason they can share
    every other stage of the algorithm:

      * ``isolated`` optimises the **body**. Only this skill is sent to the
        agent, so there is no routing decision for a description to influence
        and an edit to it could not be validated by the gate.
      * ``routing`` optimises the **description**. The body is frozen — and so
        is every other file, since only ``SKILL.md`` has a frontmatter — so a
        routing run cannot quietly become a body-optimising one judged by the
        routing guard.
    """
    entry = entry_point_for(skill_dir)
    if mode == "routing":
        return Protection(
            entry_point=entry,
            protect="body",
            readonly=frozenset(p for p in files if p != entry),
            # An `append` has no well-defined insertion point inside a `---`
            # block; guessing one hands the agent server unparseable YAML.
            allow_append=False,
        )
    if mode == "isolated":
        return Protection(entry_point=entry, protect="frontmatter")
    raise ValueError(f"unknown optimization mode {mode!r}; expected 'isolated' or 'routing'")


def render_skill(files: Mapping[str, str], skill_dir: str) -> str:
    """The whole directory as the one document every vendored stage expects.

    SkillOpt's parameter is a single markdown file, and its analyst, merge and
    ranking prompts all open with "## Current Skill" followed by that file. Ours
    is a directory, so this is the projection between the two — and it is not
    only formatting: the analyst is told to name a `path` on every edit, and the
    only way it can name one correctly is by having seen the list.

    `SKILL.md` comes first because it is the file the agent reads first, and a
    model reading top-down should meet the entry point before its references.
    """
    entry = entry_point_for(skill_dir)
    ordered = [entry] if entry in files else []
    ordered += sorted(path for path in files if path != entry)
    return "\n\n".join(
        f"### File: {path}\n```markdown\n{files[path]}\n```" for path in ordered
    )


def _line_opcodes(before: str, after: str):
    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    matcher = difflib.SequenceMatcher(None, before_lines, after_lines, autojunk=False)
    return before_lines, after_lines, matcher.get_opcodes()


def _counts(before: str, after: str) -> tuple[int, int]:
    _, _, opcodes = _line_opcodes(before, after)
    added = sum(j2 - j1 for tag, _, _, j1, j2 in opcodes if tag in ("insert", "replace"))
    removed = sum(i2 - i1 for tag, i1, i2, _, _ in opcodes if tag in ("delete", "replace"))
    return added, removed


def per_file_stats(
    before: Mapping[str, str], after: Mapping[str, str]
) -> dict[str, dict[str, int]]:
    """`{path: {"added": n, "removed": m}}` for every file that actually changed.

    Unchanged files are absent rather than present with zeroes: the diff tree
    lists what this step touched, and a wall of `+0/−0` rows would bury the one
    file that moved.
    """
    stats: dict[str, dict[str, int]] = {}
    for path in sorted(set(before) | set(after)):
        old, new = before.get(path, ""), after.get(path, "")
        if old == new:
            continue
        added, removed = _counts(old, new)
        stats[path] = {"added": added, "removed": removed}
    return stats


def total_line_changes(
    before: Mapping[str, str], after: Mapping[str, str]
) -> tuple[int, int]:
    """`(added, removed)` across the whole skill — the step row's headline."""
    stats = per_file_stats(before, after)
    return (
        sum(s["added"] for s in stats.values()),
        sum(s["removed"] for s in stats.values()),
    )


def added_lines(before: str, after: str) -> list[str]:
    """The lines this edit introduced. Context and deletions are not returned."""
    _, after_lines, opcodes = _line_opcodes(before, after)
    out: list[str] = []
    for tag, _, _, j1, j2 in opcodes:
        if tag in ("insert", "replace"):
            out.extend(after_lines[j1:j2])
    return out


def find_answer_leaks(
    before: Mapping[str, str],
    after: Mapping[str, str],
    gold_answers: Iterable[str],
) -> list[dict]:
    """Gold answers copied verbatim into the skill by this step.

    The reflect stage is shown each item's gold answer — it has to be, that is
    how it works out *why* an answer was wrong — so the optimizer is perfectly
    capable of writing "when asked about ACME Q2, answer $42,180.00". Training
    accuracy jumps and the skill is worthless.

    SkillOpt's analyst prompt forbids hardcoding question-specific values, but a
    prompt is a request. The held-out validation split is the structural defence.
    This is the visible one: the diff is read by a person, and a memorised answer
    is obvious there once it is pointed at.

    Only *added* text is searched. Flagging text that was already present would
    re-flag every later step of a run that leaked once.
    """
    wanted = [a.strip() for a in gold_answers if a and len(a.strip()) >= MIN_LEAK_CHARS]
    if not wanted:
        return []

    leaks: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for path in sorted(set(before) | set(after)):
        old, new = before.get(path, ""), after.get(path, "")
        if old == new:
            continue
        for line in added_lines(old, new):
            for answer in wanted:
                key = (path, answer)
                if key in seen or answer not in line:
                    continue
                seen.add(key)
                leaks.append({"path": path, "answer": answer, "line": line.rstrip("\n")})
    return leaks


def skill_zip(files: Mapping[str, str], manifest: dict) -> bytes:
    """The skill directory as a downloadable archive, plus what produced it.

    The manifest is not decoration: the file that lands on someone's disk is
    going to be copied onto an agent server days later, and "which run, which
    step, which score" is exactly what nobody will remember by then.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.writestr(path, files[path])
        archive.writestr(
            "manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False, default=str)
        )
    return buffer.getvalue()
