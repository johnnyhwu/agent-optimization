"""What counts as a skill on an agent server.

A skill is a directory, not a file: the agent's workspace arrives as a flat
`{relative path: text}` map, and `billing/SKILL.md` and
`billing/references/refunds.md` are two files of one skill called `billing`.
Turning that map into a list of names is one line, which is exactly why it had
started to appear in more than one place.

It matters that there is only one. Two callers ask this question — the
optimization wizard, clearing a question's skill tag against the agent before a
run is started (Decision 6: the tag and the directory name are the same name),
and the "Run eval" dialog's pre-flight, holding a whole eval set's tags against
the same list. Two implementations would eventually disagree about some path,
and the symptom would be one screen warning about a missing skill while the
other reported the same agent as complete.
"""
from __future__ import annotations

from typing import Iterable, Mapping


def top_level_skills(skills: Mapping[str, str] | Iterable[str]) -> list[str]:
    """The distinct skill names in a workspace, sorted.

    A path with no separator is its own skill: an agent that keeps one file per
    skill rather than a directory per skill is unusual, not broken, and its
    skills still have names.
    """
    return sorted({path.split("/", 1)[0] for path in skills})
