"""What counts as a skill on an agent server.

A skill is a **directory holding a `SKILL.md`**, not a file and not merely a
directory. The agent's workspace arrives as a flat `{relative path: text}` map
(`docs/agent-server-api.md` §4), so `billing/SKILL.md` and
`billing/references/refunds.md` are two files of one skill called `billing`.

It matters that there is only one implementation. Three callers ask this
question — the skills probe every screen fires while an agent URL is being
typed, the optimization wizard clearing a question's skill tag against the agent
before a run is started (Decision 6: the tag and the directory name are the same
name), and the "Run eval" dialog's pre-flight holding a whole eval set's tags
against the same list. Two implementations would eventually disagree about some
path, and the symptom would be one screen warning about a missing skill while
the other reported the same agent as complete.

**Why the entry point decides, rather than the first path segment.** This used
to be `{path.split("/", 1)[0] for path in skills}` — every distinct top-level
segment, whatever it was. That counts things that are not skills and cannot be
used as one:

    README.md                      a loose file at the root of the workspace
    LICENSE                        likewise
    shared/snippets/tone.md        a directory of material the skills include
    .github/workflows/ci.yml       whatever else the agent happens to serve

Against a workspace like that the probe reported "8 skills on this agent" where
there were three, and the number is not cosmetic: it is the count a developer
checks their agent against, and it is the list a missing-skill warning is
computed from. Everything downstream already agrees with the stricter rule —
the optimizer reads `f"{skill}/SKILL.md"` as the entry point
(`optimizer/adapter.py`, `optimizer/detector.py`), and a "skill" with no
`SKILL.md` has no body to send, no frontmatter to route on and nothing to
optimise, so naming it as one only ever promises something that fails later.

**Names carry their whole path.** A workspace that nests its skills under a
directory — `skills/billing/SKILL.md` — yields `skills/billing`, not `billing`,
because every consumer joins the name back to a path (`f"{name}/SKILL.md"`, and
the `startswith(f"{name}/")` filters in `routers/optimization.py`). A bare last
segment would name a skill that cannot be found again.
"""
from __future__ import annotations

from typing import Iterable, Mapping

# The file that makes a directory a skill. Spelled exactly, matching
# `optimizer/detector.py` and `optimizer/vendor/skill.py`: an agent serving
# `skill.md` is not serving something this platform can send back, and quietly
# accepting the variant here would produce a skill that every later step misses.
ENTRY_POINT = "SKILL.md"


def top_level_skills(skills: Mapping[str, str] | Iterable[str]) -> list[str]:
    """The skills in a workspace, by name, sorted.

    One name per `<directory>/SKILL.md` in the map — the directory's path. A
    workspace with no entry point anywhere has no skills, which is a supported
    state and an empty list, never an error: an agent may legitimately serve
    reference material and nothing else, and evaluation runs against it
    normally.

    A `SKILL.md` at the very root of the workspace is skipped rather than
    named "": it has no directory, so there is no name to hand back that any
    caller could join to a path again.
    """
    names: set[str] = set()
    for path in skills:
        directory, sep, filename = path.rpartition("/")
        if sep and filename == ENTRY_POINT and directory:
            names.add(directory)
    return sorted(names)
