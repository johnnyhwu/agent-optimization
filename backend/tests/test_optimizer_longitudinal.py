"""The epoch boundary, tested directly rather than through a whole run.

`test_optimizer_engine.py` proves the boundary fires at the right moment and
that what it produces is carried forward. This file proves what it *does* — and
it is a separate file because those contracts cannot be pinned precisely from
outside a run. The engine only ever calls this with both switches resolved, both
sides of the comparison present and a fake optimizer that always answers; each
of those is a branch here, and each has a failure mode of its own.

Nothing here touches a database. The one dependency is the optimizer seam, and
it is installed by `use_optimizer` inside the function under test.
"""
from __future__ import annotations

import json

from app.optimizer.longitudinal import run_epoch_boundary
from app.optimizer.vendor.slow_update import SLOW_UPDATE_END, SLOW_UPDATE_START

SKILL = {
    "billing/SKILL.md": "# Billing\n\n1. Quote the currency.\n",
    "billing/references/refunds.md": "Refunds take 5 days.\n",
}
PREVIOUS = {"billing/SKILL.md": "# Billing\n\n(the previous epoch's version)\n"}

ITEMS = [{"id": "set:v0", "question": "how long is a refund?"},
         {"id": "set:v1", "question": "what is the escalation limit?"}]
BEFORE = [{"id": "set:v0", "hard": 0, "soft": 0.1}, {"id": "set:v1", "hard": 1, "soft": 1.0}]
AFTER = [{"id": "set:v0", "hard": 1, "soft": 1.0}, {"id": "set:v1", "hard": 0, "soft": 0.2}]


class Optimizer:
    """Answers both boundary stages and remembers what it was asked."""

    model_name = "fake"

    def __init__(self, *, slow="new guidance", meta="new meta", fail=False):
        self.slow = slow
        self.meta = meta
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    def chat_optimizer(self, system, user, max_completion_tokens=0, retries=0,
                       stage="optimizer", timeout=None):
        self.calls.append((stage, user))
        if self.fail:
            raise RuntimeError("the optimizer endpoint is down")
        if stage == "slow_update":
            payload = {"reasoning": "r", "slow_update_content": self.slow} if self.slow else {}
        elif stage == "meta_skill":
            payload = {"meta_skill_content": self.meta} if self.meta else {}
        else:
            payload = {}
        return json.dumps(payload), {"calls": 1}

    def prompt_for(self, stage):
        return next(user for got, user in self.calls if got == stage)

    @property
    def stages(self):
        return [stage for stage, _ in self.calls]


def boundary(**over):
    kwargs = dict(
        files=SKILL, prev_files=PREVIOUS, skill_dir="billing", items=ITEMS,
        results_prev=BEFORE, results_curr=AFTER, slow_update=True,
    )
    kwargs.update(over)
    return run_epoch_boundary(**kwargs)


# --- When it declines to do anything ----------------------------------------


def test_both_switches_off_means_the_model_is_never_called():
    """The guard that makes "default off" free.

    The engine has its own check, and this is the second one on purpose: this
    function is what actually spends money, so the decision not to spend it
    belongs here too rather than only in the caller that happens to exist today.
    """
    optimizer = Optimizer()
    outcome = boundary(client=optimizer, slow_update=False, meta_skill=False)

    assert optimizer.calls == []
    assert outcome.files == SKILL
    assert outcome.changed is False


def test_a_missing_side_of_the_comparison_stops_it_before_the_call():
    """Reachable on a resumed run, and silently wrong if it is not handled.

    The epoch before a restart was executed by a process that is gone. If its
    validation results cannot be read, every sample lands in the same category
    by default and the optimizer is asked to explain a regression that is really
    an absence of data — then writes that into the skill.
    """
    optimizer = Optimizer()
    assert boundary(client=optimizer, results_prev=[]).changed is False
    assert boundary(client=optimizer, results_curr=[]).changed is False
    assert optimizer.calls == []


def test_an_optimizer_that_answers_with_nothing_leaves_the_skill_alone():
    """"No content" is a legitimate answer and must not become an empty block.

    Writing an empty protected region into `SKILL.md` would take up space the
    analyst is shown on every later step and say nothing, and the run would look
    like it had guidance when it had none.
    """
    outcome = boundary(client=Optimizer(slow="", meta=""), meta_skill=True)

    assert outcome.changed is False
    assert SLOW_UPDATE_START not in outcome.files["billing/SKILL.md"]
    assert outcome.meta_skill_text == ""


def test_an_optimizer_that_raises_does_not_take_the_boundary_with_it():
    """Upstream swallows this, and so does the run: an hour of agent calls is spent."""
    outcome = boundary(client=Optimizer(fail=True), meta_skill=True)

    assert outcome.changed is False
    assert outcome.files == SKILL


# --- What the slow update writes --------------------------------------------


def test_the_guidance_lands_in_a_protected_block_of_the_entry_point():
    """Not a reference file, and not loose prose appended to the end.

    The block is what `skill.py` refuses step-level edits inside. Guidance
    written anywhere else is ordinary text that the next analyst may rewrite or
    delete, which defeats the point of a longitudinal pass.
    """
    outcome = boundary(client=Optimizer(slow="Tighten rules; do not add them."))
    entry = outcome.files["billing/SKILL.md"]

    assert outcome.changed is True
    assert SLOW_UPDATE_START in entry and SLOW_UPDATE_END in entry
    assert "Tighten rules; do not add them." in entry
    # The original content survives, and no other file is touched.
    assert "1. Quote the currency." in entry
    assert outcome.files["billing/references/refunds.md"] == SKILL["billing/references/refunds.md"]


def test_a_second_boundary_replaces_the_block_rather_than_stacking_one():
    """Two epochs, two pieces of guidance, one block.

    Appending would grow `SKILL.md` by a paragraph per epoch and show the
    analyst several generations of advice at once, the oldest first.
    """
    first = boundary(client=Optimizer(slow="first"))
    second = run_epoch_boundary(
        files=first.files, prev_files=SKILL, skill_dir="billing", items=ITEMS,
        results_prev=BEFORE, results_curr=AFTER, client=Optimizer(slow="second"),
        slow_update=True,
    )
    entry = second.files["billing/SKILL.md"]

    assert entry.count(SLOW_UPDATE_START) == 1
    assert "second" in entry and "first" not in entry


def test_the_previous_guidance_is_shown_to_the_next_boundary():
    """The pass is asked to judge whether its own last advice worked.

    Without it each boundary is an unrelated one-shot opinion, and the skill
    accumulates advice that has never been reviewed against what happened next.
    The text has to arrive as *previous guidance*, not merely be visible inside
    the current skill — those read differently in the prompt and only one of
    them asks the question.
    """
    optimizer = Optimizer()
    run_epoch_boundary(
        # A skill with no block in it, so the only way the text can appear in
        # the prompt is through the argument.
        files=SKILL, prev_files=PREVIOUS, skill_dir="billing", items=ITEMS,
        results_prev=BEFORE, results_curr=AFTER, client=optimizer,
        prev_slow_update_text="what the last epoch concluded", slow_update=True,
    )
    prompt = optimizer.prompt_for("slow_update")

    assert "what the last epoch concluded" in prompt
    assert "Previous Slow Update Guidance" in prompt


def test_both_epochs_of_the_skill_are_put_in_front_of_the_optimizer():
    """It is a comparison of two versions, so it needs both of them."""
    optimizer = Optimizer()
    boundary(client=optimizer)
    prompt = optimizer.prompt_for("slow_update")

    assert "(the previous epoch's version)" in prompt
    assert "1. Quote the currency." in prompt


def test_the_comparison_classifies_each_sample_against_both_epochs():
    """Improved, regressed and persistent failures are three different signals.

    Collapsing them into "the score went up" is what a chart already shows. The
    value of the longitudinal pass is that one question got better *while*
    another got worse, which is invisible in an aggregate.
    """
    outcome = boundary(client=Optimizer())

    assert (outcome.n_improved, outcome.n_regressed) == (1, 1)
    assert "regressed" in outcome.reasoning or outcome.reasoning == "r"


# --- The meta skill ----------------------------------------------------------


def test_the_meta_skill_is_returned_and_never_written_into_the_skill():
    """It is advice to the editor, not content for the agent.

    Writing it into `SKILL.md` would put the optimizer's notes about its own
    editing habits in front of the agent at answer time and ship them in the
    downloaded zip.
    """
    outcome = boundary(
        client=Optimizer(slow="", meta="edit one rule at a time"),
        slow_update=False, meta_skill=True,
    )

    assert outcome.meta_skill_text == "edit one rule at a time"
    assert outcome.files == SKILL
    assert outcome.changed is False


def test_the_previous_meta_skill_is_offered_back_to_the_next_one():
    optimizer = Optimizer()
    boundary(
        client=optimizer, slow_update=False, meta_skill=True,
        prev_meta_skill_text="last epoch: narrower edits landed",
    )

    assert "last epoch: narrower edits landed" in optimizer.prompt_for("meta_skill")


def test_only_the_switches_that_are_on_cost_a_call():
    """Two passes, two prices. Turning one on must not buy the other."""
    only_slow = Optimizer()
    boundary(client=only_slow, slow_update=True, meta_skill=False)
    assert only_slow.stages == ["slow_update"]

    only_meta = Optimizer()
    boundary(client=only_meta, slow_update=False, meta_skill=True)
    assert only_meta.stages == ["meta_skill"]
