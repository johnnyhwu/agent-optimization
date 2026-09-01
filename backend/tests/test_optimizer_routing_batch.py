"""Which questions a routing step trains on.

A step reflects on its batch and rewrites the descriptions of every skill under
optimisation from it. Under the uniform shuffle the batch inherits, skill *i*
gets roughly `batch_size × nᵢ / n_train` of the questions — and routing groups
are rarely even. A workspace with one skill holding forty questions and another
holding three gives the small one nothing in most steps, and its description is
then rewritten anyway, from the evidence of the skills that did show up. That is
an edit driven by noise, applied to a parameter the gate is scoring.

So routing orders the split by interleaving the skills rather than by shuffling
it flat. It is deliberately a change to the *ordering* and not to the selection,
because the slicing on top of it is what makes an epoch cover the split exactly
once — a resampling scheme would buy per-skill coverage by giving that up.
"""
from __future__ import annotations

from app.optimizer import engine
from app.optimizer.store import Item


def items(spec: dict[str, int]) -> list[Item]:
    """`{skill: how many questions tagged for it}`, in one flat split."""
    out: list[Item] = []
    for skill, count in spec.items():
        for i in range(count):
            out.append(Item(
                item_key=f"{skill}_{i}",
                question=f"a question for {skill}",
                ground_truth_response="gt",
                ground_truth_reasoning="r",
                ordinal=len(out),
                gt_skills=(skill,),
            ))
    return out


def skills_in(batch) -> set[str]:
    return {s for entry in batch for s in entry.gt_skills}


TARGETS = ["billing", "reporting", "shipping"]


# --- what stratifying buys --------------------------------------------------


def test_a_lopsided_split_still_gives_every_skill_a_place_in_the_first_step():
    # 40 / 4 / 3. Uniformly, a batch of six is six billing questions most of the
    # time, and the two small descriptions get rewritten from questions that
    # were never about them.
    split = items({"billing": 40, "reporting": 4, "shipping": 3})
    batch = engine.train_batch(
        split, epoch_no=1, step_in_epoch=1, batch_size=6, seed=1, targets=TARGETS,
    )
    assert skills_in(batch) == set(TARGETS)


def test_the_same_split_uniformly_sampled_can_miss_a_skill_entirely():
    """The behaviour being fixed, pinned so the fix is not mistaken for a no-op."""
    split = items({"billing": 40, "reporting": 4, "shipping": 3})
    batch = engine.train_batch(split, epoch_no=1, step_in_epoch=1, batch_size=6, seed=1)
    assert skills_in(batch) != set(TARGETS)


def test_every_skill_appears_before_any_skill_appears_twice():
    split = items({"billing": 10, "reporting": 10, "shipping": 10})
    batch = engine.train_batch(
        split, epoch_no=1, step_in_epoch=1, batch_size=3, seed=3, targets=TARGETS,
    )
    assert skills_in(batch) == set(TARGETS)


# --- what stratifying must not cost -----------------------------------------


def test_an_epoch_still_covers_the_split_exactly_once():
    # The property the slicing depends on. Stratifying reorders; it does not
    # resample, so no question is trained on twice and none is skipped.
    split = items({"billing": 5, "reporting": 4, "shipping": 3})
    batches = [
        engine.train_batch(
            split, epoch_no=1, step_in_epoch=step, batch_size=4, seed=1, targets=TARGETS,
        )
        for step in (1, 2, 3)
    ]
    keys = [entry.item_key for batch in batches for entry in batch]
    assert sorted(keys) == sorted(entry.item_key for entry in split)


def test_the_same_seed_and_epoch_reproduce_the_same_batch():
    # The composition is never stored; a resumed run recomputes it.
    split = items({"billing": 5, "reporting": 4})
    once = engine.train_batch(
        split, epoch_no=2, step_in_epoch=1, batch_size=3, seed=42, targets=TARGETS,
    )
    twice = engine.train_batch(
        split, epoch_no=2, step_in_epoch=1, batch_size=3, seed=42, targets=TARGETS,
    )
    assert [i.item_key for i in once] == [i.item_key for i in twice]


def test_a_later_epoch_reshuffles_within_the_strata():
    split = items({"billing": 8, "reporting": 8})
    first = engine.train_batch(
        split, epoch_no=1, step_in_epoch=1, batch_size=4, seed=1, targets=TARGETS,
    )
    later = engine.train_batch(
        split, epoch_no=2, step_in_epoch=1, batch_size=4, seed=1, targets=TARGETS,
    )
    assert [i.item_key for i in first] != [i.item_key for i in later]


def test_a_question_tagged_for_no_target_is_still_trained_on():
    # It cannot be scored — `routing_scores` skips an untagged question — but
    # dropping it here would shrink the split silently and make the epoch stop
    # covering it, which is a different and worse problem than not scoring it.
    split = items({"billing": 3})
    split.append(Item(
        item_key="orphan", question="q", ground_truth_response="gt",
        ground_truth_reasoning="r", ordinal=99, gt_skills=(),
    ))
    seen = [
        entry.item_key
        for step in (1, 2)
        for entry in engine.train_batch(
            split, epoch_no=1, step_in_epoch=step, batch_size=2, seed=1, targets=TARGETS,
        )
    ]
    assert "orphan" in seen
    assert sorted(seen) == sorted(entry.item_key for entry in split)


def test_a_question_tagged_for_two_skills_is_trained_on_once():
    # Routing groups overlap by design — that question is the evidence for where
    # a boundary belongs — but placing it once per group would train and score
    # on it twice.
    split = items({"billing": 2})
    split.append(Item(
        item_key="spans", question="q", ground_truth_response="gt",
        ground_truth_reasoning="r", ordinal=99, gt_skills=("billing", "reporting"),
    ))
    batch = engine.train_batch(
        split, epoch_no=1, step_in_epoch=1, batch_size=3, seed=1, targets=TARGETS,
    )
    assert [i.item_key for i in batch].count("spans") == 1


def test_isolated_is_left_exactly_as_it_was():
    # `targets` empty is the whole gate. An isolated run sends one skill to the
    # agent and has no strata to interleave.
    split = items({"billing": 6})
    assert [
        i.item_key for i in engine.train_batch(
            split, epoch_no=1, step_in_epoch=1, batch_size=3, seed=5,
        )
    ] == [
        i.item_key for i in engine.train_batch(
            split, epoch_no=1, step_in_epoch=1, batch_size=3, seed=5, targets=(),
        )
    ]
