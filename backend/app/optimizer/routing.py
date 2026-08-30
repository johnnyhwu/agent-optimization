"""How well a rollout routed: the skills read against the skills tagged.

This is what `routing` mode is gated on, and it replaces a proxy. The old guard
required the target skill's activation rate not to fall, which catches the one
degenerate strategy it was written for — narrow the description until the agent
stops opening the skill, and every question it answered badly gets answered from
the model's own knowledge instead — and misses the mirror image entirely. A
description widened until it wins every question keeps its own activation at
100% while starving every other skill on the agent, and an activation-only guard
calls that an improvement.

Scored per question, because that is where the right answer lives:

    hard = 1 when the set of skills read is exactly the set tagged
    soft = F1 between the two sets

`hard` is a set **equality**, not "were the tagged ones among them". Opening a
skill that was not this question's job is a routing error — it is precisely what
an over-broad description produces — and a metric that ignored it would score
the degenerate strategy perfectly.

Both numbers are produced because `hard` alone is harsh. Over a few dozen
validation questions a strict set match moves in large steps and can sit at zero
for the first several, which leaves the gate's "strictly greater" nothing to
work with; F1 separates "read one of the two right ones" from "read neither", so
there is a direction to move in. Which one actually gates is the run's
`gate_metric`, exactly as it is for judge accuracy — no new machinery.

Three answers, as everywhere else in this package. A question is left out of the
fraction when it could not be measured rather than counted as a miss: no
trajectory landed (Langfuse lags and sometimes fails), the question carries no
tags to be right or wrong about, or the question failed for a reason the skill
cannot fix. Counting any of those as zero would hand the gate a collapse that
never happened.
"""
from __future__ import annotations

from typing import Mapping, Sequence

from app.optimizer.store import Item, ResultRow


def score_one(read: set[str], tagged: set[str]) -> tuple[float, float]:
    """`(hard, soft)` for one question. Both sets are non-empty by the caller."""
    hard = 1.0 if read == tagged else 0.0
    if not read:
        return hard, 0.0
    overlap = len(read & tagged)
    if not overlap:
        return hard, 0.0
    precision = overlap / len(read)
    recall = overlap / len(tagged)
    return hard, 2 * precision * recall / (precision + recall)


def routing_scores(
    rows: Sequence[ResultRow], items: Sequence[Item] | Mapping[str, Item] | None,
) -> tuple[float | None, float | None]:
    """`(hard, soft)` across a rollout, or `(None, None)` if nothing was measurable.

    `None` and not `0.0`: a rollout whose traces never landed has not told us
    the agent routed badly, it has told us nothing, and the gate must be able to
    tell those apart.
    """
    if not items:
        return None, None
    tags = {
        item.item_key: set(item.gt_skills)
        for item in (items.values() if isinstance(items, Mapping) else items)
    }

    hard_total = soft_total = 0.0
    n = 0
    for row in rows:
        if row.status != "done" or row.skills_read is None:
            continue
        tagged = tags.get(row.item_key) or set()
        if not tagged:
            continue
        hard, soft = score_one(set(row.skills_read), tagged)
        hard_total += hard
        soft_total += soft
        n += 1

    if not n:
        return None, None
    return hard_total / n, soft_total / n
