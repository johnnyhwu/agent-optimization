"""What a routing analyst reads instead of trajectories.

A routing run optimises the `description` line that decides whether an agent
opens a skill. That decision is made from descriptions alone, **before** the
agent does anything, so one rollout's whole contribution to it is the triple

    (the question, the skills it was tagged for, the skills the agent opened)

which `reflection.analyst_item` already extracts. The conversation that follows
is evidence about the *answer*; the tool catalogue and the agent's reply are
evidence about neither. Sending them costs thousands of characters per question,
which is what forces a minibatch down to eight — and eight questions is a
keyhole to rewrite a parameter that governs all of them through.

So routing sends the triple and nothing else. At roughly a line per question a
whole training batch fits in less than one of the old eight-trajectory prompts,
which is what makes the single full-batch analyst call in `update._reflect`
affordable in the first place.

Two renderers, and the reason each is shaped the way it is:

**`render_digest` is a confusion matrix, not a list.** A flat list of a hundred
questions asks the analyst to do the grouping itself, and the grouping *is* the
finding: which skill is losing which class of question, and to whom. Grouped, a
blurred boundary between two descriptions is a block on the page rather than a
pattern to infer.

**`system_prompt_view` prints the agent's own setup once, folded.** Routing
failures are not always the description's fault — a system prompt that tells the
agent to answer directly, or that carries its own routing rules, produces
exactly the same symptom — and an analyst that cannot see it will keep editing
descriptions against a cause no description controls. It could not simply be
hoisted: `trajectory.shared_preamble` requires *exact* equality across the batch
and a single injected timestamp defeats that, so the fold below keeps what every
run shared and marks what varied, rather than picking one run's clock and
presenting it as everyone's.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.optimizer.skillio import _opcodes

# Roughly a line per question, so this holds a batch of several hundred and
# still leaves the skills, the competitors and the model's own answer inside an
# ordinary context window. Deliberately **not** `reflect_budget_chars`: that one
# is named for trajectories and measured against them, and routing no longer
# sends any — reusing it would mean a wizard field that appears to bound
# something it has nothing to do with.
DEFAULT_DIGEST_BUDGET_CHARS = 120_000

# One question is a routing signal, not a document. Past this, what is left is
# the tail of a paragraph that said what it was about in its first sentence.
MAX_QUESTION_CHARS = 200

# How many questions one bucket lists before the budget starts biting. Tried in
# order; the first that fits is used, so a batch that fits keeps everything and
# a batch that does not loses depth evenly rather than losing whole buckets.
_CAP_LADDER = (None, 50, 20, 10, 5, 3, 1)

# Below this share of lines in common, the variants have no skeleton worth
# showing and splicing them would produce a prompt no question ran under.
SIMILARITY_FLOOR = 0.7

# How many concrete values a `«varies»` marker illustrates itself with. Two is
# enough to show *what kind* of thing varies — a clock, an id — which is all a
# reader needs to decide whether it matters. Fifty would be the field itself.
_MAX_SAMPLES = 2


# --- the agent's setup ------------------------------------------------------


@dataclass(frozen=True)
class Divergence:
    """Whether the batch was answered under one agent setup, and how far from it.

    `diverged` is the load-bearing field and it means something narrower than
    "the prompts differed": it means they differed *too much to show as one*, so
    what the analyst was given is one variant standing in for the rest. A run
    where only a timestamp moved is not diverged — nothing about the agent
    changed — and warning about it every time would train the reader to ignore
    the warning that matters.
    """

    n_prompts: int = 0
    n_variants: int = 0
    majority_share: float = 1.0
    diverged: bool = False
    tools_diverged: bool = False


def _normalise_tools(tools: Sequence[Any] | None) -> tuple:
    """A tool catalogue as an order-independent identity.

    `shared_preamble` compares the list, so a server that returns its tools in a
    different order on each call reads as an agent that was told something
    different. It was not, and a spurious divergence warning is worse than none.
    """
    if not tools:
        return ()
    names = []
    for tool in tools:
        if isinstance(tool, Mapping):
            names.append(str(tool.get("name") or tool.get("function") or tool))
        else:
            names.append(str(tool))
    return tuple(sorted(names))


def _common_lines(variants: Sequence[list[str]]) -> list[str]:
    """The lines every variant has, in order.

    Folded pairwise because `_opcodes` is pairwise. Intersecting an *ordered*
    subsequence repeatedly is what keeps the result a real skeleton — a set
    intersection would lose the order and let the marker land in the wrong place.
    """
    common = list(variants[0])
    for variant in variants[1:]:
        if not common:
            break
        common = [
            line
            for tag, i1, i2, _, _ in _opcodes(common, variant)
            if tag == "equal"
            for line in common[i1:i2]
        ]
    return common


def _gaps(common: list[str], lines: list[str]) -> list[list[str]]:
    """What `lines` carries around each common line: `len(common) + 1` segments.

    `common` is a subsequence of `lines` by construction, so every common line
    aligns and everything else falls into the gap before it (or, for the last
    segment, after all of them).
    """
    gaps: list[list[str]] = [[] for _ in range(len(common) + 1)]
    if not common:
        gaps[0] = list(lines)
        return gaps
    index = 0
    for tag, i1, i2, j1, j2 in _opcodes(common, lines):
        if tag == "equal":
            index = i2
        else:
            gaps[index].extend(lines[j1:j2])
    return gaps


def _varies_marker(segments: list[list[str]], n_prompts: int) -> str:
    """One elision, illustrated by a couple of the values it stands for."""
    seen: list[str] = []
    for segment in segments:
        text = "\n".join(segment).strip()
        if text and text not in seen:
            seen.append(text)
    samples = " / ".join(f'"{s}"' for s in seen[:_MAX_SAMPLES])
    count = f"{len(seen)} distinct values across {n_prompts} runs"
    return f"«varies ({count}), e.g. {samples}»" if samples else f"«varies ({count})»"


def system_prompt_view(
    prompts: Sequence[str], tools: Sequence[Sequence[Any]] | None = None,
) -> tuple[str, Divergence]:
    """The frozen agent setup, as one block, plus what it had to elide to be one.

    Three outcomes, and the third is the point of the other two existing:

    * one distinct prompt — printed verbatim, which is the common case and the
      one `shared_preamble` already handled;
    * many that share most of their lines — the shared lines verbatim, each
      run of differing ones replaced by a `«varies»` marker naming a couple of
      the values. This is the timestamp and workspace-id case, and it is
      **not** reported as divergence: nothing about the agent changed;
    * many that do not — no splice. A prompt assembled from lines that never
      appeared together is a document no question was answered under, and an
      analyst reasoning from it is reasoning about a system that does not
      exist. The majority variant is printed whole and labelled as a stand-in,
      and `diverged` is set so the run can say so out loud.
    """
    texts = [p for p in prompts if p]
    tools_diverged = len({_normalise_tools(t) for t in (tools or [])}) > 1
    if not texts:
        return "", Divergence(n_prompts=len(prompts), tools_diverged=tools_diverged)

    counts: dict[str, int] = {}
    for text in texts:
        counts[text] = counts.get(text, 0) + 1
    variants = list(counts)
    majority, majority_n = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
    base = Divergence(
        n_prompts=len(texts),
        n_variants=len(variants),
        majority_share=majority_n / len(texts),
        tools_diverged=tools_diverged,
    )

    if len(variants) == 1:
        return majority, base

    split = [v.splitlines() for v in variants]
    common = _common_lines(split)
    longest = max(len(lines) for lines in split)
    if not longest or len(common) / longest < SIMILARITY_FLOOR:
        header = (
            f"(representative — the setup {majority_n} of {len(texts)} questions ran "
            f"under; {len(variants)} variants differ too much to show as one)\n"
        )
        return header + majority, Divergence(**{**base.__dict__, "diverged": True})

    per_variant = [_gaps(common, lines) for lines in split]
    out: list[str] = []
    for index in range(len(common) + 1):
        segments = [gaps[index] for gaps in per_variant]
        rendered = {"\n".join(s) for s in segments}
        if len(rendered) == 1:
            out.extend(segments[0])
        else:
            out.append(_varies_marker(segments, len(texts)))
        if index < len(common):
            out.append(common[index])
    return "\n".join(out) + "\n", base


# --- the confusion matrix ---------------------------------------------------


def _question(item: Mapping) -> str:
    text = " ".join(str(item.get("task_description") or "").split())
    if len(text) <= MAX_QUESTION_CHARS:
        return text or "(no question text)"
    return text[:MAX_QUESTION_CHARS].rstrip() + "…"


@dataclass
class _Bucket:
    """One row of the matrix: a verdict, and the questions that earned it."""

    label: str
    items: list[Mapping]


def _skill_buckets(items: Sequence[Mapping], skill: str) -> tuple[list[_Bucket], int, int]:
    """How `skill`'s own questions fared, plus `(tagged, reached)`."""
    tagged = [i for i in items if skill in (i.get("gt_skills") or ())]
    reached, nothing, unmeasured = [], [], []
    instead: dict[tuple[str, ...], list[Mapping]] = {}
    for entry in tagged:
        read = entry.get("skills_read")
        if read is None:
            unmeasured.append(entry)
        elif skill in read:
            reached.append(entry)
        elif not read:
            nothing.append(entry)
        else:
            instead.setdefault(tuple(sorted(read)), []).append(entry)

    buckets = [_Bucket(f"✓ opened {skill}", reached)] if reached else []
    for read, entries in sorted(instead.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        buckets.append(_Bucket(f"✗ opened {', '.join(read)} instead", entries))
    if nothing:
        buckets.append(_Bucket("✗ opened nothing at all", nothing))
    if unmeasured:
        # Never folded into a miss. A trace that did not land says nothing about
        # how the agent routed, and counting it as a failure would invite an
        # edit against evidence that does not exist.
        buckets.append(_Bucket("· not measured (no trace landed)", unmeasured))
    return buckets, len(tagged), len(reached)


def _misfire_buckets(items: Sequence[Mapping], skill: str) -> list[_Bucket]:
    """Questions belonging to another skill that opened this one anyway.

    The mirror image of a miss, and the half an activation-rate view cannot see:
    a description widened until it wins everything keeps its own activation at
    100% while starving every other skill on the agent.
    """
    by_owner: dict[tuple[str, ...], list[Mapping]] = {}
    for entry in items:
        tagged = entry.get("gt_skills") or ()
        read = entry.get("skills_read")
        if read is None or skill in tagged or skill not in read:
            continue
        by_owner.setdefault(tuple(sorted(tagged)), []).append(entry)
    return [
        _Bucket(f"← tagged {', '.join(owner) or '(nothing)'}", entries)
        for owner, entries in sorted(by_owner.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    ]


def _render(buckets: Sequence[_Bucket], cap: int | None) -> list[str]:
    lines: list[str] = []
    for bucket in buckets:
        lines.append(f"{bucket.label} ({len(bucket.items)})")
        shown = bucket.items if cap is None else bucket.items[:cap]
        lines.extend(f"- {_question(entry)}" for entry in shown)
        hidden = len(bucket.items) - len(shown)
        if hidden:
            lines.append(f"  ({hidden} more not shown)")
    return lines


def _pct(part: int, whole: int) -> str:
    return f"{round(100 * part / whole)}%" if whole else "—"


def render_digest(
    items: Sequence[Mapping],
    targets: Sequence[str],
    *,
    budget_chars: int = DEFAULT_DIGEST_BUDGET_CHARS,
) -> str:
    """Every question in the batch, grouped by what the routing did with it.

    Two different numbers appear and they are deliberately labelled apart. The
    header carries the **gated** metric — the exact set match `routing.py`
    scores and the gate compares — over the questions that could be measured.
    Each skill's heading carries how many of *its own* questions reached it,
    which is the number an edit to *that* description can move. Reporting one as
    the other is how a run ends up optimising against a figure it is not judged
    on.
    """
    if not items:
        return ""

    measured = [i for i in items if i.get("skills_read") is not None]
    exact = sum(1 for i in measured if float(i.get("hard") or 0.0))
    header = (
        f"## Routing Results ({len(items)} questions, {len(measured)} measured, "
        f"{_pct(exact, len(measured))} routed exactly right)"
    )

    sections: list[tuple[str, list[_Bucket]]] = []
    for skill in sorted(targets):
        buckets, n_tagged, n_reached = _skill_buckets(items, skill)
        if not n_tagged:
            # Said out loud rather than omitted. A skill missing from the page
            # reads as one that is doing fine; a skill with no questions has no
            # evidence at all, and editing its description is editing on noise.
            sections.append((
                f"### {skill} — no questions in this batch are tagged for it",
                [],
            ))
            continue
        sections.append((
            f"### {skill} — {n_tagged} tagged · reached by {n_reached} "
            f"({_pct(n_reached, n_tagged)})",
            buckets,
        ))
        misfires = _misfire_buckets(items, skill)
        if misfires:
            total = sum(len(b.items) for b in misfires)
            noun = "question" if total == 1 else "questions"
            sections.append((
                f"### Misfired into {skill} — {total} {noun} tagged elsewhere opened it",
                misfires,
            ))

    # The widest cap whose output fits. Buckets shrink together rather than the
    # first ones being rendered whole and the last ones vanishing, so what the
    # analyst loses is depth in every group instead of whole groups it is never
    # told about.
    for cap in _CAP_LADDER:
        body: list[str] = []
        for title, buckets in sections:
            body.append(title)
            body.extend(_render(buckets, cap))
            body.append("")
        text = "\n".join([header, ""] + body).rstrip() + "\n"
        if len(text) <= budget_chars or cap == _CAP_LADDER[-1]:
            return text
    return text
