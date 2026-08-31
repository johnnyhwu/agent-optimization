"""Which skills a question actually loaded, from the trace it left behind.

Everything downstream assumes an answer to this. In `isolated` mode a low
activation rate means optimising the body is pointless — the agent never read it
— and the run should say so rather than spend an hour producing edits nobody
will execute. In `routing` mode it *is* the objective: what the description is
optimised for is that the right skill gets opened, and which skills were opened
instead is the measurement.

**One rule.** A skill counts as read when its own body text appears somewhere the
agent was *shown* it:

  * the **system prompt** it was set up with — whole classes of agent inject
    skills there and never call a tool for them;
  * the result of a **tool** it called — which tool does not matter, and
    deliberately so.

Nothing is injected to make this visible. The markers are lines of the skill
exactly as it was sent, because a probe token would put a new variable into the
very context whose effect on the model is being measured, and measuring that
effect is this platform's whole job.

**Body, never frontmatter.** An agent that lists every skill's `description` in
its system prompt so it can choose between them has only been *offered* this
skill; only body text proves it was *loaded*. Collapsing the two scores every
skill as read on every question — 100% before anything has been optimised.

**Assistant turns are excluded.** They are the model's output, not its input. A
good answer that restates a rule is not evidence the rule was read, and counting
it was the false positive that made the previous whole-payload search
untrustworthy.

Three answers, not two. When no trajectory landed the result is **unknown** and
not "read nothing": Langfuse ingestion lags and sometimes fails outright, and
reporting that as the agent having read nothing blames the agent for the trace
store — and, since routing accuracy is computed from these, would reject every
candidate in a run that then ends having learned nothing, with no indication
why. A trajectory that *did* land and shows no body text is evidence, because a
run that could not be seen into at all never gets past the pre-flight.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from app.optimizer.skillio import frontmatter_span
from app.optimizer.trajectory import Trajectory

# A body line shorter than this is not evidence: "Do it." would match by
# coincidence in half the traces ever recorded.
MIN_MARKER_CHARS = 24

# How many distinct body lines to look for per skill. One is enough to prove the
# text arrived; a handful makes the detector robust to a step editing the line
# it happened to pick.
MARKER_COUNT = 3

_ENTRY = "SKILL.md"


@dataclass
class Activation:
    """What one rollout's trace showed about the skills it was sent."""

    # Whether the *run's* skill was read. None only when nothing could be seen —
    # see the module note; it must never harden into a negative.
    activated: bool | None
    # Every skill whose body appeared, sorted — or None when nothing could be
    # seen at all. `None` and `[]` are different answers and the difference is
    # load-bearing: routing accuracy skips the first and scores the second as
    # "opened nothing", which is a routing failure. Defaulting this to `[]`
    # alongside `activated=None` made a trace that never landed count against
    # the candidate, which is the Langfuse outage the tri-state exists to
    # survive.
    skills_read: list[str] | None = None
    # system_prompt | tool | none — where the target's evidence was found.
    hit: str = "none"


def _body_of(files: Mapping[str, str], skill_name: str) -> str:
    """Everything of this skill that is not its frontmatter, as one blob."""
    entry = f"{skill_name}/{_ENTRY}"
    text = files.get(entry, "")
    span = frontmatter_span(text)
    body = text[span[1]:] if span else text

    # Reference files are body: reading one proves the skill was loaded, and
    # skills routinely instruct exactly that ("for refunds, read
    # references/refunds.md").
    for path, content in sorted(files.items()):
        if path != entry and path.startswith(f"{skill_name}/"):
            body += "\n" + content
    return body


def skill_names(files: Mapping[str, str]) -> list[str]:
    """The skill directories present in a workspace, sorted."""
    return sorted({path.split("/", 1)[0] for path in files if "/" in path})


def skill_markers(files: Mapping[str, str], skill_name: str) -> list[str]:
    """Distinctive lines of one skill's body, longest first.

    Longest first because a long line is both less likely to collide by chance
    and more likely to survive a step that rewords something shorter. Matching is
    line-based rather than on a fixed-size block of the file: a tool returning a
    file adds line numbers, elides the middle, or normalises whitespace, and any
    of those breaks one long block silently while leaving most lines intact.
    """
    lines = {
        stripped
        for line in _body_of(files, skill_name).splitlines()
        if len(stripped := line.strip()) >= MIN_MARKER_CHARS
    }
    return sorted(lines, key=len, reverse=True)[:MARKER_COUNT]


def build_markers(files: Mapping[str, str]) -> dict[str, list[str]]:
    """`{skill: markers}` for every skill in a workspace, computed once.

    Worth hoisting: routing compares against the whole workspace on every item
    of every rollout, and in routing mode the other skills are frozen for the
    life of the run.
    """
    return {name: skill_markers(files, name) for name in skill_names(files)}


def shown_to_model(trajectory: Trajectory) -> str:
    """Everything the agent was *shown*, and nothing it produced.

    Stated as "everything except `assistant`" rather than as a list of the roles
    that count, because the list would be wrong. A tool result reaches us under
    at least three roles depending on the agent: `tool` in the OpenAI shape,
    `user` in the Anthropic one (a `tool_result` content part inside the user
    message), and `span` when the trace store logged the tool as an observation
    of its own rather than as a message. Enumerating those means a fourth
    dialect scores 0% activation silently — which is exactly how the tool-path
    detector this replaces used to fail.

    Only the assistant's own turns are output, and only they are excluded. The
    theoretical cost is a question that quotes 24+ characters of a skill body
    back verbatim; the cost of the alternative is whole agent dialects reading
    as agents that never open a skill.
    """
    parts = [trajectory.system_prompt or ""]
    parts += [turn.text or "" for turn in trajectory.turns if turn.role != "assistant"]
    return "\n".join(parts)


def read_skills(
    trajectory: Trajectory | None, markers: Mapping[str, list[str]]
) -> set[str] | None:
    """The skills this question loaded, or None when nothing could be seen.

    A skill with no markers at all — a body too short for any line to qualify —
    can never be read. That is the safe direction: the alternative, matching
    nothing and calling it a hit, would report every such skill as read on every
    question.
    """
    if trajectory is None or (not trajectory.system_prompt and not trajectory.turns):
        return None

    visible = shown_to_model(trajectory)
    return {
        name
        for name, lines in markers.items()
        if lines and any(line in visible for line in lines)
    }


def detect_activation(
    trajectory: Trajectory | None,
    *,
    skill_name: str,
    skill_files: Mapping[str, str],
    workspace_files: Mapping[str, str] | None = None,
    markers: Mapping[str, list[str]] | None = None,
) -> Activation:
    """One rollout's row: was the target read, and what else was.

    `workspace_files` is everything the call sent — the target plus, in routing
    mode, the frozen baseline. Isolated sends one skill, so the same code reports
    at most that one and needs no special case.
    """
    if markers is None:
        markers = build_markers({**(workspace_files or {}), **skill_files})

    read = read_skills(trajectory, markers)
    if read is None:
        return Activation(activated=None, skills_read=None, hit="none")

    activated = skill_name in read
    hit = "none"
    if activated:
        own = markers.get(skill_name) or []
        in_system = any(line in (trajectory.system_prompt or "") for line in own)
        hit = "system_prompt" if in_system else "tool"

    return Activation(activated=activated, skills_read=sorted(read), hit=hit)


def entry_body_visible(
    trajectory: Trajectory | None, *, skill_name: str, skill_files: Mapping[str, str]
) -> bool:
    """Does this trajectory carry the **entry point's own** body text?

    The one question that makes a missing probe marker mean anything. The marker
    can only ever sit in `<skill>/SKILL.md`, so its absence is evidence only
    where that file's text would have been visible had it arrived.

    **Reference files are deliberately excluded**, which is the opposite of what
    `read_skills` counts and why this is a separate function. Reading a reference
    file legitimately proves the skill was loaded — but it says nothing about the
    marker, and skills routinely instruct exactly that. Counting it would accuse
    an agent of ignoring an override at the moment it was following the very
    skill we sent.
    """
    entry = f"{skill_name}/{_ENTRY}"
    if trajectory is None or entry not in skill_files:
        return False
    markers = skill_markers({entry: skill_files[entry]}, skill_name)
    visible = shown_to_model(trajectory)
    return any(line in visible for line in markers)
