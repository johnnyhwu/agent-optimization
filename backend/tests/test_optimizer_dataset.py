"""Choosing what a run trains on: grouping by skill, and the train/validation split.

Everything here happens once, in a wizard, before any money is spent — and it
decides what the whole run means. A question that lands in the wrong group is
reflected on by an analyst editing a skill it has nothing to do with. A
validation split that got all the easy questions produces a baseline no
candidate can beat, and an hour later the developer has a chart of rejections
and no idea why.

None of that fails loudly. That is what these tests are for.
"""
from __future__ import annotations

import uuid

import pytest

from app.optimizer.dataset import (
    MIN_TRAIN,
    MIN_VAL,
    WARN_VAL,
    Candidate,
    default_split,
    group_by_skill,
    item_key,
    split_issues,
    split_item_key,
)

SET_A = uuid.uuid4()
SET_B = uuid.uuid4()


def candidate(qid, *, skills=("billing",), eval_set_id=SET_A, accuracy=None, runs=0,
              name="set A") -> Candidate:
    return Candidate(
        item_key=item_key(eval_set_id, qid),
        question_id=qid,
        question=f"question {qid}",
        ground_truth_response=f"answer {qid}",
        ground_truth_reasoning="reasoning",
        eval_set_id=eval_set_id,
        eval_set_name=name,
        skills=tuple(skills),
        prior_accuracy=accuracy,
        prior_runs=runs,
    )


# --- Identity ---------------------------------------------------------------


def test_the_same_question_id_in_two_eval_sets_stays_two_items():
    """`question_id` is unique per eval set, not globally.

    Two sets both holding `q_1` is routine — it is what a
    download-edit-re-upload cycle produces — and if the run keyed items by
    `question_id` alone the two would collapse into one. The training set would
    silently shrink, one of the two questions would never be answered, and the
    item that survived would carry the other's gold answer.
    """
    a = candidate("q_1", eval_set_id=SET_A)
    b = candidate("q_1", eval_set_id=SET_B)
    assert a.item_key != b.item_key
    assert a.question_id == b.question_id


def test_an_item_key_survives_a_round_trip_through_the_wizard():
    """The browser sends back keys, not rows. They have to mean one thing.

    The split editor works on `item_key` strings; `POST /runs` resolves them
    back to questions. A key that could not be parsed back — or that matched two
    questions — would put the wrong gold answer on a training item, which is the
    one error a rollout cannot detect.
    """
    key = item_key(SET_A, "q_1")
    assert key.startswith(str(SET_A))
    assert key.endswith("q_1")


def test_a_question_id_containing_the_separator_is_still_unambiguous():
    """Uploaded ids are developer-supplied text, so one will contain a colon.

    Splitting on the *first* separator is what keeps `…:invoice:2024:q7` meaning
    question `invoice:2024:q7` rather than question `invoice`. Getting this
    wrong sends `POST /runs` looking for a question that does not exist — or,
    worse, finds a different one and pairs it with the wrong gold answer.
    """
    key = item_key(SET_A, "invoice:2024:q7")
    prefix, question_id = split_item_key(key)
    assert prefix == str(SET_A)
    assert question_id == "invoice:2024:q7"


# --- Grouping by skill ------------------------------------------------------


@pytest.mark.parametrize("mode", ["isolated", "routing"])
def test_questions_are_grouped_by_their_one_skill(mode):
    groups, ambiguous = group_by_skill([
        candidate("q1", skills=("billing",)),
        candidate("q2", skills=("billing",)),
        candidate("q3", skills=("reporting",)),
    ], mode=mode)
    assert sorted(groups) == ["billing", "reporting"]
    assert [c.question_id for c in groups["billing"]] == ["q1", "q2"]
    assert ambiguous == []


@pytest.mark.parametrize("mode", ["isolated", "routing"])
def test_a_question_with_no_skill_goes_to_the_ambiguous_bucket(mode):
    """It cannot be assigned, and guessing is worse than saying so.

    An unlabelled question put into a skill group by default would be reflected
    on by an analyst editing a skill the question may have nothing to do with —
    and the run would look completely normal while learning from it.
    """
    groups, ambiguous = group_by_skill([
        candidate("q1", skills=()),
        candidate("q2", skills=("billing",)),
    ], mode=mode)
    assert [c.question_id for c in ambiguous] == ["q1"]
    assert [c.question_id for c in groups["billing"]] == ["q2"]


def test_a_routing_run_puts_a_question_with_several_skills_in_each_group():
    """Two tags is evidence about both skills, and a routing run uses it as such.

    It used to go to the ambiguous bucket beside the untagged ones, for a reason
    that held while a run optimised exactly one skill: training `billing` on a
    question tagged `billing` and `reporting` attributes to `billing` a failure
    that may belong entirely to the other.

    A routing run optimises the descriptions of several skills together and
    scores each question against *all* the skills it is tagged with — a question
    belonging to both is precisely the case that says where the boundary between
    two descriptions should fall. Dropping it discarded the most informative
    questions in the set.
    """
    groups, ambiguous = group_by_skill([
        candidate("q1", skills=("billing", "reporting")),
    ], mode="routing")

    assert ambiguous == []
    assert [c.question_id for c in groups["billing"]] == ["q1"]
    assert [c.question_id for c in groups["reporting"]] == ["q1"]


def test_only_an_untagged_question_is_unassignable_in_routing():
    groups, ambiguous = group_by_skill([
        candidate("q1", skills=()),
        candidate("q2", skills=("billing", "reporting")),
    ], mode="routing")

    assert [c.question_id for c in ambiguous] == ["q1"]
    assert set(groups) == {"billing", "reporting"}


def test_every_candidate_is_placed_somewhere():
    """Nothing is silently dropped, so the wizard's counts describe the whole set.

    A multi-tagged question is now in more than one group, which means the group
    counts add up to more than the number of questions — the wizard says so
    rather than hiding it.
    """
    candidates = [
        candidate("q1", skills=("billing",)),
        candidate("q2", skills=()),
        candidate("q3", skills=("billing", "x")),
        candidate("q4", skills=("reporting",)),
    ]
    groups, ambiguous = group_by_skill(candidates, mode="routing")
    placed = {c.item_key for group in groups.values() for c in group}
    placed |= {c.item_key for c in ambiguous}

    assert placed == {c.item_key for c in candidates}
    assert sum(len(g) for g in groups.values()) == 4, "q3 counted under both its tags"


def test_groups_come_back_in_a_stable_order():
    """The wizard lists skills; a list that reshuffles per request is unusable."""
    candidates = [candidate(f"q{i}", skills=(s,))
                  for i, s in enumerate(["reporting", "billing", "escalation"])]
    grouped = group_by_skill(candidates, mode="isolated")[0]
    assert list(grouped) == ["billing", "escalation", "reporting"]


# --- The default split ------------------------------------------------------


def test_the_default_split_is_seventy_thirty():
    train, val = default_split([candidate(f"q{i}") for i in range(20)])
    assert len(train) == 14
    assert len(val) == 6


def test_every_question_lands_in_exactly_one_split():
    """A dropped question is a training set quietly smaller than the screen says.

    And a duplicated one is worse: it would be in both splits, which weakens the
    gate in precisely the way the overlap warning exists to flag — except
    silently, with nothing to warn about.
    """
    candidates = [candidate(f"q{i}") for i in range(17)]
    train, val = default_split(candidates)
    keys = [c.item_key for c in train] + [c.item_key for c in val]
    assert sorted(keys) == sorted(c.item_key for c in candidates)


def test_the_split_is_deterministic():
    """The same set of questions must produce the same proposal twice.

    The wizard re-fetches when a developer steps back and forward again. A split
    that reshuffled would silently discard the adjustments they had made.
    """
    candidates = [candidate(f"q{i}", accuracy=(i % 5) / 4) for i in range(20)]
    first = default_split(candidates)
    second = default_split(candidates)
    assert [c.item_key for c in first[0]] == [c.item_key for c in second[0]]
    assert [c.item_key for c in first[1]] == [c.item_key for c in second[1]]


def test_the_two_splits_end_up_with_comparable_difficulty():
    """An all-hard validation split makes the gate useless, and order can cause it.

    The gate accepts a candidate only if it *beats* the current skill on
    validation. If validation drew the questions the agent always fails, the
    baseline sits near the floor and every reading is noise; if it drew the ones
    the agent always passes, the baseline sits at the ceiling, every candidate
    ties, and the run rejects everything for an hour with a chart that shows
    nothing wrong.

    The input order below is adversarial on purpose: the hard questions sit
    exactly on the positions an evenly-spaced quota would pick. Taking the
    questions in the order they arrived — however evenly spaced the picks are —
    hands validation every hard question and training every easy one. Sorting by
    difficulty first is what makes the result independent of the order the
    preview happened to return.
    """
    # The positions an even 70/30 quota lands on, for 21 questions.
    picked = [i for i in range(21) if int((i + 1) * 0.3) > int(i * 0.3)]
    candidates = [
        candidate(f"q{i:02d}", accuracy=0.0 if i in picked else 1.0, runs=5)
        for i in range(21)
    ]

    train, val = default_split(candidates)
    train_mean = sum(c.prior_accuracy for c in train) / len(train)
    val_mean = sum(c.prior_accuracy for c in val) / len(val)

    assert abs(train_mean - val_mean) < 0.5, (
        f"training averages {train_mean:.2f} and validation {val_mean:.2f} — "
        "the split is tracking the input order rather than the difficulty"
    )
    assert any(c.prior_accuracy == 0.0 for c in val)
    assert any(c.prior_accuracy == 1.0 for c in val)


def test_a_question_that_has_never_been_run_is_not_treated_as_the_hardest():
    """'No prior data' is a third category, not an accuracy of zero.

    Folded in as 0.0 it sorts among the questions the agent always fails —
    which is the end of the ordering a developer weights most heavily, and the
    end that decides which split it lands in. Unknown belongs after the known
    ones, as its own band.
    """
    # The ids sort the never-run questions *first*, so only the unknown band in
    # the sort key can put them last. Naming them the other way round would let
    # the id tiebreaker produce the right answer for the wrong reason.
    candidates = (
        [candidate(f"zzz_known{i}", accuracy=0.0, runs=3) for i in range(6)]
        + [candidate(f"aaa_new{i}") for i in range(6)]
    )
    train, _ = default_split(candidates)
    unknown_positions = [i for i, c in enumerate(train) if c.prior_runs == 0]
    known_positions = [i for i, c in enumerate(train) if c.prior_runs > 0]
    assert min(unknown_positions) > max(known_positions), (
        "never-run questions are ordered among the always-failing ones"
    )


def test_a_tiny_set_still_puts_something_in_each_split():
    """Rounding must not produce an empty validation split.

    The size gate below refuses a run this small anyway, but `default_split` is
    also what the wizard renders while the developer is still adding sources —
    and a screen showing `0 validation` before they have finished choosing reads
    as a bug in the tool rather than a fact about their data.
    """
    train, val = default_split([candidate(f"q{i}") for i in range(3)])
    assert len(train) >= 1
    assert len(val) >= 1


# --- What blocks a run, and what merely warns -------------------------------


def test_a_split_at_the_minimum_is_allowed():
    """The boundary is inclusive, so the message and the check agree.

    A limit that says "at least 1" and refuses 1 is the kind of thing someone
    spends twenty minutes on before concluding the tool is broken.
    """
    issues = split_issues(
        train_keys=[f"k{i}" for i in range(MIN_TRAIN)],
        val_keys=[f"v{i}" for i in range(MIN_VAL)],
    )
    assert not [i for i in issues if i["level"] == "error"]


def test_a_tiny_split_warns_but_still_runs():
    """The case the old floor of 8/5 refused, and the reason it was lowered.

    Three questions is a bad experiment and a perfectly good check that the
    pipeline works before an hour of agent calls is spent on sixty. The tool
    says which one it thinks this is; the developer decides.
    """
    issues = split_issues(
        train_keys=["a", "b", "c"],
        val_keys=["d", "e"],
    )
    assert not [i for i in issues if i["level"] == "error"]
    codes = {i["code"] for i in issues}
    assert codes == {"train_too_small", "val_too_small"}


def test_each_column_raises_exactly_one_size_issue():
    """Three tiers, and a column belongs to one of them.

    `elif` rather than three `if`s: a split of six training questions is small
    *and* below the comfortable threshold, and saying so twice would put two
    boxes on screen describing one number.
    """
    for n_train, expected in ((0, "train_empty"), (3, "train_too_small"),
                              (12, "train_small"), (30, None)):
        issues = split_issues(
            train_keys=[f"k{i}" for i in range(n_train)],
            val_keys=[f"v{i}" for i in range(WARN_VAL)],
        )
        train_codes = [i["code"] for i in issues if i["code"].startswith("train")]
        assert train_codes == ([expected] if expected else [])


def test_a_small_but_workable_split_warns_without_blocking():
    """It is the developer's call, and they can only make it if they are told."""
    issues = split_issues(
        train_keys=[f"k{i}" for i in range(10)],
        val_keys=[f"v{i}" for i in range(6)],
    )
    assert not [i for i in issues if i["level"] == "error"]
    codes = {i["code"] for i in issues}
    assert "train_small" in codes and "val_small" in codes


def test_a_comfortable_split_produces_no_issues_at_all():
    issues = split_issues(
        train_keys=[f"k{i}" for i in range(40)],
        val_keys=[f"v{i}" for i in range(20)],
    )
    assert issues == []


def test_a_question_in_both_splits_is_warned_about_by_name():
    """Overlap is allowed on purpose, and it breaks the gate.

    'Duplicate to validation' is a deliberate feature — with few questions a
    developer may want one counted twice. But a question the skill was just
    edited *for* is not held-out data, and validation accuracy stops being an
    honest estimate of anything. The run still goes ahead; the warning is what
    makes it a decision rather than an accident, and naming the questions is
    what makes it fixable.
    """
    issues = split_issues(train_keys=["a", "b", "c"], val_keys=["c", "d"])
    overlap = next(i for i in issues if i["code"] == "overlap")
    assert overlap["level"] == "warning"
    assert overlap["item_keys"] == ["c"]


def test_an_empty_split_is_an_error_rather_than_a_crash():
    issues = split_issues(train_keys=[], val_keys=[])
    assert {i["code"] for i in issues if i["level"] == "error"} == {
        "train_empty", "val_empty"
    }


def test_an_empty_validation_split_does_not_divide_by_zero():
    """Why the floor is 1 and not 0.

    The tier below `SOFT_VAL` reports how far one answer moves accuracy, which
    is `100 / n_val`. Guarding it with `n_val >= MIN_VAL` only works while
    `MIN_VAL` is at least 1 — at 0 the empty case would fall through to it and
    the message would read "moves accuracy by inf points".
    """
    issues = split_issues(train_keys=["a"], val_keys=[])
    assert [i["code"] for i in issues if i["code"].startswith("val")] == ["val_empty"]
    assert all("inf" not in i["message"] for i in issues)


def test_an_isolated_run_still_excludes_a_question_tagged_with_several_skills():
    """The hazard that justified excluding them has not gone away for isolated.

    An isolated run optimises one skill against the questions in its group.
    Training `billing` on a question tagged `billing` *and* `reporting`
    attributes to `billing` a failure that may belong entirely to the other, and
    the run looks completely normal while doing it. Routing is what makes such a
    question usable — it optimises both descriptions together and scores the
    question against all of its tags — and that reasoning does not carry across
    to a mode where the other skill is neither sent nor editable.
    """
    groups, ambiguous = group_by_skill([
        candidate("q1", skills=("billing", "reporting")),
        candidate("q2", skills=("billing",)),
    ], mode="isolated")

    assert [c.question_id for c in ambiguous] == ["q1"]
    assert [c.question_id for c in groups["billing"]] == ["q2"]
    assert "reporting" not in groups


def test_the_two_modes_disagree_only_about_the_multi_tagged_question():
    """Everything else about grouping is the mode's business to leave alone."""
    candidates = [
        candidate("q1", skills=("billing",)),
        candidate("q2", skills=()),
        candidate("q3", skills=("billing", "reporting")),
    ]
    isolated, iso_out = group_by_skill(candidates, mode="isolated")
    routing, rout_out = group_by_skill(candidates, mode="routing")

    assert [c.question_id for c in iso_out] == ["q2", "q3"]
    assert [c.question_id for c in rout_out] == ["q2"]
    assert [c.question_id for c in isolated["billing"]] == ["q1"]
    assert [c.question_id for c in routing["billing"]] == ["q1", "q3"]
