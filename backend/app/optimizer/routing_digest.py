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

from dataclasses import dataclass, replace
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
    # How many of them carried no system prompt at all. Counted rather than
    # quietly excluded: the block is headed "every question below was answered
    # under this", and a batch where half the rows recorded no setup did not
    # agree about anything — it was only half observed. Same rule the matrix
    # below it follows for a missing trace.
    n_missing: int = 0
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


# The largest DP table one fold pair may build, after the shared head and tail
# have been trimmed off. `_opcodes` is an O(n×m) table in pure Python, so a pair
# still differing over hundreds of lines once their common ends are gone costs
# seconds — per pair, across every variant, twice per step. Two prompts that far
# apart are also exactly the ones `SIMILARITY_FLOOR` refuses to splice, so the
# budget and the floor say the same thing and the expensive way of finding out
# is skipped.
_MAX_FOLD_CELLS = 250_000


def _fold_opcodes(before: list[str], after: list[str]) -> list[tuple] | None:
    """`_opcodes` with the common head and tail trimmed off first.

    Two agent setups from one batch are near-identical by construction — that is
    the premise of folding them — so the interesting part is a handful of lines
    in an otherwise shared document. `_opcodes` does not know that: it fills the
    whole `n × m` table, which for a 1,200-line preamble is 1.4 million Python
    cells *per pair*, and this fold runs over every variant and then again for
    `_batch_chars`.

    Trimming is exact, not an approximation: when the first lines of both
    sequences are equal some optimal LCS matches them, so the trimmed ends can
    be re-attached as `equal` runs without changing the result. `_opcodes` itself
    is deliberately left alone — `frontend/src/diff.js` draws the same
    alignment, and this is a local concern, not a diff one.

    `None` means "further apart than is worth folding", which the caller reads
    the same way it reads a similarity below the floor.
    """
    n, m = len(before), len(after)
    head = 0
    while head < n and head < m and before[head] == after[head]:
        head += 1
    tail = 0
    while (
        tail < n - head and tail < m - head
        and before[n - 1 - tail] == after[m - 1 - tail]
    ):
        tail += 1

    if (n - head - tail) * (m - head - tail) > _MAX_FOLD_CELLS:
        return None
    if not head and not tail:
        return _opcodes(before, after)

    out: list[tuple] = []
    if head:
        out.append(("equal", 0, head, 0, head))
    out.extend(
        (tag, i1 + head, i2 + head, j1 + head, j2 + head)
        for tag, i1, i2, j1, j2 in _opcodes(before[head:n - tail], after[head:m - tail])
    )
    if tail:
        out.append(("equal", n - tail, n, m - tail, m))
    return out


def _common_lines(variants: Sequence[list[str]]) -> list[str] | None:
    """The lines every variant has, in order.

    Folded pairwise because `_opcodes` is pairwise. Intersecting an *ordered*
    subsequence repeatedly is what keeps the result a real skeleton — a set
    intersection would lose the order and let the marker land in the wrong place.

    `None` when two of them are too far apart to fold, which the caller treats
    exactly as it treats a similarity below the floor.
    """
    common = list(variants[0])
    for variant in variants[1:]:
        if not common:
            break
        opcodes = _fold_opcodes(common, variant)
        if opcodes is None:
            return None
        common = [
            line
            for tag, i1, i2, _, _ in opcodes
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
    # Never `None` here: `_common_lines` already folded this pair and returned,
    # so the trimmed middle is at most the one it measured.
    for tag, i1, i2, j1, j2 in _fold_opcodes(common, lines) or _opcodes(common, lines):
        if tag == "equal":
            index = i2
        else:
            gaps[index].extend(lines[j1:j2])
    return gaps


def _varies_marker(segments: list[list[str]], n_prompts: int) -> str:
    """One elision, illustrated by a couple of the values it stands for."""
    seen: list[str] = []
    absent = False
    for segment in segments:
        text = "\n".join(segment).strip()
        if not text:
            # Named, not skipped. This marker is only reached because the
            # segments differ, so an empty one means the runs that carried
            # nothing here — and "some agents were told this and others were
            # not" is the single most routing-relevant thing this fold can
            # find. Dropping it left the marker claiming one distinct value for
            # a difference that was entirely about presence.
            absent = True
        elif text not in seen:
            seen.append(text)

    values = len(seen) + (1 if absent else 0)
    shown = seen[:_MAX_SAMPLES - 1] if absent else seen[:_MAX_SAMPLES]
    samples = [f'"{s}"' for s in shown] + (["absent"] if absent else [])
    count = f"{_n(values, 'distinct value')} across {_n(n_prompts, 'run')}"
    joined = " / ".join(samples)
    return f"«varies ({count}), e.g. {joined}»" if joined else f"«varies ({count})»"


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
    * many that do not — or that are too far apart for `_fold_opcodes` to fold
      within its budget, which is the same condition arrived at more cheaply —
      no splice. A prompt assembled from lines that never
      appeared together is a document no question was answered under, and an
      analyst reasoning from it is reasoning about a system that does not
      exist. The majority variant is printed whole and labelled as a stand-in,
      and `diverged` is set so the run can say so out loud.
    """
    texts = [p for p in prompts if p]
    missing = len(prompts) - len(texts)
    tools_diverged = len({_normalise_tools(t) for t in (tools or [])}) > 1
    if not texts:
        return "", Divergence(
            n_prompts=len(prompts), n_missing=missing, n_variants=0,
            majority_share=0.0, tools_diverged=tools_diverged,
        )

    counts: dict[str, int] = {}
    for text in texts:
        counts[text] = counts.get(text, 0) + 1
    variants = list(counts)
    majority, majority_n = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
    # Shares are over every question the block speaks for, not over the ones
    # that recorded a setup. The section is headed "every question below was
    # answered under this", so a batch half of which recorded nothing is a
    # batch this block half describes — and a `majority_share` of 1.0 over the
    # recorded half would report it as uniform.
    base = Divergence(
        n_prompts=len(prompts),
        n_missing=missing,
        n_variants=len(variants),
        majority_share=majority_n / len(prompts),
        tools_diverged=tools_diverged,
    )
    unrecorded = (
        f"(the setup for {missing} of {len(prompts)} questions was not recorded; "
        f"what follows is the {len(texts)} that were)\n"
        if missing else ""
    )

    if len(variants) == 1:
        return unrecorded + majority, base

    split = [v.splitlines() for v in variants]
    common = _common_lines(split)
    longest = max(len(lines) for lines in split)
    if common is None or not longest or len(common) / longest < SIMILARITY_FLOOR:
        header = (
            f"(representative — the setup {majority_n} of {len(texts)} questions ran "
            f"under; {len(variants)} variants differ too much to show as one)\n"
        )
        return (
            unrecorded + header + majority,
            replace(base, diverged=True),
        )

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
    return unrecorded + "\n".join(out) + "\n", base


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


def _skill_buckets(
    items: Sequence[Mapping], skill: str,
) -> tuple[list[_Bucket], int, int, int]:
    """How `skill`'s own questions fared, plus `(tagged, measured, reached)`.

    `measured` is separate from `tagged` for the same reason the header counts
    them separately: a question whose trace never landed is not a question the
    skill failed to attract. A skill whose every trace was lost would otherwise
    be reported as "reached by 0 (0%)" — a description condemned on evidence
    that does not exist, which is exactly the edit this digest should not invite.
    """
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
    return buckets, len(tagged), len(tagged) - len(unmeasured), len(reached)


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
    """A share, or a dash when there was nothing to take a share of.

    Never `0%` for an empty denominator: "none of them" and "none of nothing"
    are different claims, and only one of them is about the skill.
    """
    return f"{round(100 * part / whole)}%" if whole else "—"


def _n(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


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

    `budget_chars` is honoured in two stages: every bucket loses depth together
    down the cap ladder, and only when that bottoms out do whole sections go,
    with a notice naming how many. It has a floor, the way
    `truncate_trajectory`'s `min_keep` does — a header, one section and one
    question is the smallest well-formed digest, and a budget under that is
    exceeded rather than met. That floor is a few hundred characters against a
    default of 120,000, and at the default a batch of 600 questions across a
    dozen skills fits with room to spare; a budget small enough to hit it is a
    misconfiguration, and returning a malformed matrix would serve it worse
    than returning a slightly oversized one.
    """
    if not items:
        return ""

    # Exactly what `routing.routing_scores` counts, and for the same two
    # reasons: a trace that never landed measured nothing, and a question
    # carrying no tags has no right answer to be judged against. The second is
    # the one that bites — `analyst_item` has no routing verdict to give an
    # untagged question, so it falls back to the *judge's*, and reading that
    # field here would report a batch nobody could route as perfectly routed.
    # `_stratify` keeps untagged questions and places them at the tail, so a
    # late step in an epoch can be made largely of them.
    measured = [
        i for i in items
        if i.get("skills_read") is not None and (i.get("gt_skills") or ())
    ]
    exact = sum(1 for i in measured if float(i.get("hard") or 0.0))
    untagged = sum(1 for i in items if not (i.get("gt_skills") or ()))
    header = (
        f"## Routing Results ({_n(len(items), 'question')}, {len(measured)} measured, "
        f"{_pct(exact, len(measured))} routed exactly right"
        + (f" · {untagged} tagged for no skill under optimisation" if untagged else "")
        + ")"
    )

    sections: list[tuple[str, list[_Bucket]]] = []
    for skill in sorted(targets):
        buckets, n_tagged, n_measured, n_reached = _skill_buckets(items, skill)
        if not n_tagged:
            # Said out loud rather than omitted. A skill missing from the page
            # reads as one that is doing fine; a skill with no questions has no
            # evidence at all, and editing its description is editing on noise.
            sections.append((
                f"### {skill} — no questions in this batch are tagged for it",
                [],
            ))
        else:
            # Over what was measured, never over what was tagged. The two differ
            # only when traces went missing, and that is precisely when the
            # difference matters.
            lost = n_tagged - n_measured
            sections.append((
                f"### {skill} — {_n(n_tagged, 'question')} tagged · reached by "
                f"{n_reached} of the {n_measured} measured ({_pct(n_reached, n_measured)})"
                + (f" · {lost} not measured" if lost else ""),
                buckets,
            ))
        # Outside the branch above, because a skill with no questions of its own
        # is exactly where the misfires are the *only* evidence there is. That
        # is the over-broad description at its worst — one that has attracted
        # every other skill's questions — and skipping its misfires would leave
        # its section saying there is nothing here to learn from.
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
    def render(cap: int | None, keep: int) -> str:
        body: list[str] = []
        for title, buckets in sections[:keep]:
            body.append(title)
            body.extend(_render(buckets, cap))
            body.append("")
        dropped = len(sections) - keep
        if dropped:
            body.append(
                f"({dropped} more section(s) omitted — this batch did not fit the "
                "digest budget. The skills they describe are still editable and "
                "still scored; you are simply not being shown their questions.)"
            )
        return "\n".join([header, ""] + body).rstrip() + "\n"

    for cap in _CAP_LADDER:
        text = render(cap, len(sections))
        if len(text) <= budget_chars:
            return text

    # Every bucket is down to one question and it still does not fit, which
    # takes an unusually small budget or an unusually wide workspace. Whole
    # sections go now, and the notice says so — because the alternative is
    # returning a prompt that overflows the optimizer's context window, and that
    # does not truncate anything: the call is refused and the step loses its
    # gradient entirely. A digest missing a section is worse than one that fits
    # and better than none at all.
    for keep in range(len(sections) - 1, 0, -1):
        text = render(_CAP_LADDER[-1], keep)
        if len(text) <= budget_chars:
            return text
    return render(_CAP_LADDER[-1], 1)
