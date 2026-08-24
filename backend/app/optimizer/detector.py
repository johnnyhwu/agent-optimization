"""Did the agent actually load the skill being optimised?

Everything downstream assumes it did. In `isolated` mode a low activation rate
means optimising the body is pointless — the agent never read it — and the run
should say so rather than spend an hour producing edits nobody will execute. In
`routing` mode activation *is* the objective, and it additionally guards the gate.

Two detectors, because the two plausible agent designs leave different evidence:

  * **tool path** — the agent reads `.../skills/billing/SKILL.md` through a tool
    call, so the path is right there in the trace. This also answers a question a
    boolean cannot: *which* skills were read. "It read `reporting` instead of
    you" is a far stronger signal for the routing analyst than "it did not read
    you".
  * **content match** — the agent has the skill injected into its prompt, so
    there is no tool call to find, but the text itself is in the payload.

The marker for the content detector is a line **of the skill's own body**.
Nothing is injected: a probe token would put a new variable into the very context
whose effect on the model we are trying to measure, and this platform's entire
job is measuring that effect.

Body and frontmatter are matched separately, and the distinction carries routing
mode. An agent that lists every skill's `description` in its system prompt so it
can choose between them has been **offered** this skill; only body text proves it
was **loaded**. Collapsing the two would score routing mode at 100% before it
optimised anything.

The third answer matters as much as the other two. When neither detector fires
and nothing has yet established that either *could* fire for this agent, the
result is **unknown**, not false — reporting 0% activation for an agent whose
skill-loading is simply invisible to us would condemn a perfectly good run.
`detectable` is how the caller says "pre-flight proved a detector works here", at
which point absence becomes evidence.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from app.integrations.base import Trace
from app.optimizer.skillio import frontmatter_span

# `<anything>/skill|skills/<name>/<rest>`. Deployments differ in the prefix, the
# case and — on Windows-shaped paths — the separator, but not in this shape.
DEFAULT_PATH_PATTERNS: tuple[str, ...] = (
    r"(?:^|[/\\])skills?[/\\]([A-Za-z0-9._-]+)[/\\]",
)

# A body line shorter than this is not evidence: "Do it." would match by
# coincidence in half the traces ever recorded.
MIN_MARKER_CHARS = 24

# How many distinct body lines to look for. One is enough to prove the text
# arrived; a handful makes the detector robust to a step editing the line it
# happened to pick.
MARKER_COUNT = 3


@dataclass
class Activation:
    """What the detectors saw for one rollout item."""

    # True when a detector fired. False only when the caller has established
    # that a detector *would* have fired. None otherwise — see the module note.
    activated: bool | None
    # Every skill the trace shows being read, first-seen order, de-duplicated.
    skills_read: list[str] = field(default_factory=list)
    # tool_path | content | none. The stronger evidence wins when both fire.
    hit: str = "none"
    # The skill's description reached the model, but its body did not: the agent
    # was offered this skill and chose something else. Routing mode's signal.
    offered: bool = False


def payload_text(trace: Trace) -> str:
    """Everything the model was shown or produced, as one searchable blob."""
    parts: list[str] = []
    for span in trace.spans:
        for body in (span.input, span.output):
            if body:
                parts.append(body)
    return "\n".join(parts)


def _tool_call_paths(trace: Trace) -> list[str]:
    """Every string that could be a file path in a tool call's arguments.

    Deliberately not "the `path` argument of the `read_file` tool": agents name
    that tool `read_file`, `Read`, `view_file` and `fs.read`, and the argument
    `path`, `file_path` or `target`. Scanning the argument *values* costs nothing
    and does not need a list of every agent's vocabulary — the skill-directory
    shape is distinctive enough on its own.
    """
    found: list[str] = []
    for span in trace.spans:
        for payload in (span.output_json, span.input_json):
            for call in _walk_tool_calls(payload):
                arguments = call.get("function", {}).get("arguments")
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except (ValueError, TypeError):
                        found.append(arguments)
                        continue
                if isinstance(arguments, Mapping):
                    found.extend(v for v in arguments.values() if isinstance(v, str))
                elif isinstance(arguments, str):
                    found.append(arguments)
    return found


def _walk_tool_calls(payload: Any) -> Iterable[dict]:
    if isinstance(payload, Mapping):
        calls = payload.get("tool_calls")
        if isinstance(calls, list):
            for call in calls:
                if isinstance(call, Mapping):
                    yield dict(call)
        for value in payload.values():
            if isinstance(value, (Mapping, list)):
                yield from _walk_tool_calls(value)
    elif isinstance(payload, list):
        for item in payload:
            if isinstance(item, (Mapping, list)):
                yield from _walk_tool_calls(item)


def _skills_from_paths(paths: Iterable[str], patterns: Iterable[str]) -> list[str]:
    compiled = [re.compile(p, re.IGNORECASE) for p in patterns]
    seen: list[str] = []
    for raw in paths:
        for pattern in compiled:
            match = pattern.search(raw)
            if not match:
                continue
            name = match.group(1)
            if name.lower() not in {s.lower() for s in seen}:
                seen.append(name)
            break
    return seen


def _markers(skill_files: Mapping[str, str], skill_name: str) -> tuple[list[str], list[str]]:
    """`(body markers, frontmatter markers)` — distinctive lines of each region.

    Longest first: a long line is both less likely to collide by chance and more
    likely to survive a step that rewords something shorter.
    """
    entry = f"{skill_name}/SKILL.md"
    text = skill_files.get(entry, "")
    span = frontmatter_span(text)
    front_text, body_text = ("", text)
    if span:
        front_text, body_text = text[span[0]:span[1]], text[span[1]:]

    # Reference files are body: reading one proves the skill was loaded.
    for path, content in skill_files.items():
        if path != entry and path.startswith(f"{skill_name}/"):
            body_text += "\n" + content

    def pick(source: str, *, values_only: bool = False) -> list[str]:
        lines = set()
        for line in source.splitlines():
            line = line.strip()
            if values_only:
                # Frontmatter markers are the *values*, not the YAML. An agent
                # that lists skills for routing writes "- billing: Invoices,
                # balances…", not "description: Invoices, balances…" — matching
                # the key line would miss every real menu.
                if line in ("---", ""):
                    continue
                _, _, value = line.partition(":")
                line = value.strip().strip("\"'") or line
            if len(line) >= MIN_MARKER_CHARS:
                lines.add(line)
        return sorted(lines, key=len, reverse=True)[:MARKER_COUNT]

    return pick(body_text), pick(front_text, values_only=True)


def detect_activation(
    trace: Trace | None,
    *,
    skill_name: str,
    skill_files: Mapping[str, str],
    path_patterns: Iterable[str] = DEFAULT_PATH_PATTERNS,
    detectable: bool = False,
) -> Activation:
    """Whether this rollout loaded `skill_name`, and what else it read."""
    # No trace is not the same as a trace showing no skill read. Langfuse
    # ingestion lags and sometimes fails; treating that as "the agent ignored the
    # skill" would blame the agent for the trace store being unreachable.
    if trace is None or not trace.spans:
        return Activation(activated=None, hit="none")

    skills_read = _skills_from_paths(_tool_call_paths(trace), path_patterns)
    target = skill_name.lower()
    # A path may spell the target differently from the run's own name (`Billing/`
    # on a case-insensitive filesystem). Canonicalise that one so aggregating
    # activation across a rollout does not split it into two skills; the others
    # keep the spelling the trace gave, which is the agent's own truth.
    skills_read = [skill_name if name.lower() == target else name for name in skills_read]
    if any(name.lower() == target for name in skills_read):
        return Activation(activated=True, skills_read=skills_read, hit="tool_path", offered=True)

    body_markers, front_markers = _markers(skill_files, skill_name)
    payload = payload_text(trace)
    body_seen = any(marker in payload for marker in body_markers)
    front_seen = any(marker in payload for marker in front_markers)

    if body_seen:
        read = skills_read or [skill_name]
        if not any(name.lower() == target for name in read):
            read = [*read, skill_name]
        return Activation(activated=True, skills_read=read, hit="content", offered=True)

    # Nothing proved the skill was loaded. Whether that is a "no" or a "cannot
    # tell" is the caller's to know: only the pre-flight rollout can establish
    # that a detector works against this particular agent.
    return Activation(
        activated=False if (detectable or skills_read) else None,
        skills_read=skills_read,
        hit="none",
        offered=front_seen,
    )
