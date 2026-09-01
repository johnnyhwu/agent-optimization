"""One analyst call per routing step, over the whole batch, both verdicts at once.

Routing optimises a description — a decision boundary written as one line of
frontmatter — and the machinery it inherited from SkillOpt was built for a
skill *body*. Three consequences, and this module pins the fix for each:

**Edits collide.** An isolated edit appends to one section of a long document,
so two minibatches' edits are near orthogonal and merging them is real merging.
A routing edit replaces the same single line. N minibatches produce N mutually
exclusive rewrites of it, and the merge stage then chooses among them while
seeing only the edits — never the questions that produced them. So routing makes
**one** call and there is nothing to choose between.

**Both verdicts describe the same boundary.** Failures are the questions that
should have come in; successes are the ones already inside that must not be lost.
Split across two analysts, one proposes a narrowing blind to what it breaks and
the other a widening blind to the misfires. Together they are a single
constrained problem, so routing does not split them — and `failure_only`, which
exists to drop one side, has nothing to drop.

**The trajectory is not evidence about routing.** The choice is made from
descriptions before the agent acts, so the prompt carries the digest instead.
That is also what makes the whole batch affordable in one call.

Isolated keeps every one of these behaviours. The tests that pin *that* live in
`test_optimizer_update.py` and must pass untouched.
"""
from __future__ import annotations

import json
import threading

from app.optimizer.trajectory import Trajectory, Turn
from app.optimizer.update import run_update_stage

FILES = {
    "billing/SKILL.md": (
        "---\n"
        "name: billing\n"
        "description: Invoices, credit notes and outstanding balances.\n"
        "---\n"
        "\n"
        "# Billing\n"
        "\n"
        "1. Quote figures in the account's own currency.\n"
    ),
    "reporting/SKILL.md": (
        "---\n"
        "name: reporting\n"
        "description: Monthly revenue reports.\n"
        "---\n"
        "\n"
        "# Reporting\n"
    ),
}

SYSTEM_PROMPT = "\n".join(
    [
        "You are a support agent for Acme Cloud.",
        "Answer the user's question.",
        "Consult the skills below when one applies.",
        "If none applies, answer from your own knowledge.",
        "Never invent an order number.",
        "Keep replies under six sentences.",
    ]
)


class Recorder:
    """An `OptimizerClient` that answers with no edits and keeps every prompt."""

    model_name = "scripted"

    def __init__(self, analyst=None):
        self._analyst = analyst
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def chat_optimizer(self, system, user, max_completion_tokens=16384,
                       retries=3, stage="optimizer", timeout=None):
        with self._lock:
            self.calls.append({"stage": stage, "system": system, "user": user})
        usage = {"calls": 1, "prompt_tokens": 10, "completion_tokens": 5}
        if stage == "ranking":
            return json.dumps({"selected_indices": [0]}), usage
        if stage == "merge":
            return json.dumps({"reasoning": "merged", "edits": []}), usage
        body = self._analyst or {"batch_size": 1, "patch": {"reasoning": "", "edits": []}}
        return json.dumps(body), usage

    def analyst_prompts(self) -> list[str]:
        return [c["user"] for c in self.calls if c["stage"] == "analyst"]


def item(key, *, tagged, read, question=None, system=SYSTEM_PROMPT):
    tagged = sorted(tagged)
    return {
        "id": key,
        "hard": 0.0 if read is None else float(sorted(read) == tagged),
        "soft": 0.0,
        "task_description": question or f"question {key}",
        "reference_text": f"the gold answer for {key}",
        "agent_response": f"the agent said something about {key}",
        "fail_reason": "",
        "gt_skills": tagged,
        "skills_read": None if read is None else sorted(read),
        "n_turns": 1,
        "trajectory": Trajectory(
            system_prompt=system,
            turns=[Turn(role="assistant", text=f"a long conversation about {key}")],
        ),
    }


BATCH = [
    item("a", tagged=["billing"], read=["billing"]),
    item("b", tagged=["billing"], read=["reporting"]),
    item("c", tagged=["reporting"], read=["reporting"]),
    item("d", tagged=["reporting"], read=[]),
]


def run(**overrides):
    kwargs = dict(
        files=FILES,
        skill_dir=["billing", "reporting"],
        mode="routing",
        items=BATCH,
        client=Recorder(),
        edit_budget=4,
        minibatch_size=2,
        analyst_workers=2,
        merge_batch_size=8,
        seed=7,
    )
    kwargs.update(overrides)
    return run_update_stage(**kwargs)


# --- one call, both verdicts ------------------------------------------------


def test_a_routing_step_makes_exactly_one_analyst_call_however_small_the_minibatch():
    # `minibatch_size=2` against four questions would be two calls under the
    # inherited splitting, and each would rewrite the same line from a keyhole.
    client = Recorder()
    outcome = run(client=client)
    assert len(client.analyst_prompts()) == 1
    assert len(outcome.minibatches) == 1
    assert outcome.minibatches[0].n_items == len(BATCH)


def test_the_one_call_carries_the_successes_and_the_failures_together():
    client = Recorder()
    run(client=client)
    prompt = client.analyst_prompts()[0]
    for key in ("question a", "question b", "question c", "question d"):
        assert key in prompt


def test_the_combined_call_is_recorded_as_such_rather_than_as_a_failure_batch():
    # The page badges this value. Calling a batch that contains both verdicts
    # "failure" would misdescribe what the analyst was asked.
    outcome = run()
    assert outcome.minibatches[0].source_type == "combined"


def test_failure_only_has_nothing_to_drop_in_routing():
    # The flag exists to withhold the successes. In routing they are the
    # constraint that stops the description narrowing until it wins nothing.
    client = Recorder()
    run(client=client, failure_only=True)
    prompt = client.analyst_prompts()[0]
    assert len(client.analyst_prompts()) == 1
    assert "question a" in prompt and "question c" in prompt


def test_every_question_reaches_the_one_call():
    outcome = run()
    assert sorted(outcome.minibatches[0].item_keys) == ["a", "b", "c", "d"]


# --- what the prompt carries ------------------------------------------------


def test_the_routing_prompt_carries_the_confusion_matrix():
    client = Recorder()
    run(client=client)
    prompt = client.analyst_prompts()[0]
    assert "## Routing Results" in prompt
    assert "### billing" in prompt
    assert "opened nothing at all" in prompt


def test_the_routing_prompt_sends_no_trajectory():
    client = Recorder()
    run(client=client)
    prompt = client.analyst_prompts()[0]
    assert "a long conversation about" not in prompt
    assert "#### Conversation" not in prompt


def test_the_routing_prompt_sends_neither_answer():
    # Routing's ground truth is which skill to open, not what to say. Sending
    # the gold answer would be paying for text that cannot inform the edit —
    # and it is the only thing an answer-leak check has to worry about.
    client = Recorder()
    run(client=client)
    prompt = client.analyst_prompts()[0]
    assert "the gold answer for" not in prompt
    assert "the agent said something about" not in prompt


def test_the_agents_own_setup_is_shown_once_and_marked_frozen():
    client = Recorder()
    run(client=client)
    prompt = client.analyst_prompts()[0]
    assert "FROZEN" in prompt
    assert "Consult the skills below when one applies." in prompt
    # Once, not once per question.
    assert prompt.count("Never invent an order number.") == 1


def test_a_setup_that_only_differs_by_a_timestamp_still_shows_the_instructions():
    batch = [
        item("a", tagged=["billing"], read=["billing"],
             system=SYSTEM_PROMPT + f"\nToday is 2026-08-30 14:0{i} UTC")
        for i in range(4)
    ]
    client = Recorder()
    run(client=client, items=batch)
    prompt = client.analyst_prompts()[0]
    assert "Never invent an order number." in prompt
    assert "varies" in prompt


def test_the_competitors_and_the_skills_under_optimisation_both_still_travel():
    client = Recorder()
    run(client=client, context_files={"other/SKILL.md": (
        "---\nname: other\ndescription: Something else entirely.\n---\n\n# Other\n"
    )})
    prompt = client.analyst_prompts()[0]
    assert "## Current Skill" in prompt
    assert "Something else entirely." in prompt


def test_the_sections_are_in_the_order_a_reader_needs_them():
    client = Recorder()
    run(client=client)
    prompt = client.analyst_prompts()[0]
    setup = prompt.index("FROZEN")
    skill = prompt.index("## Current Skill")
    results = prompt.index("## Routing Results")
    assert setup < skill < results


# --- what happens after the one call ----------------------------------------


def test_one_patch_needs_no_merge_and_no_ranking_call():
    # `_hierarchical_merge` returns a lone patch unchanged and `rank_and_select`
    # returns a pool already inside the budget unchanged, both without calling
    # the model. So a routing step spends nothing on stages that have nothing to
    # decide — and the page must not then claim they were merely unrecorded.
    client = Recorder(analyst={
        "batch_size": 4,
        "patch": {
            "reasoning": "narrow billing",
            "edits": [{
                "op": "replace",
                "path": "billing/SKILL.md",
                "target": "description: Invoices, credit notes and outstanding balances.",
                "content": "description: Refunds, invoices and payment failures.",
            }],
        },
    })
    outcome = run(client=client)
    assert [c["stage"] for c in client.calls] == ["analyst"]
    assert outcome.n_edits_applied == 1
    assert "Refunds, invoices and payment failures." in outcome.files["billing/SKILL.md"]


def test_both_descriptions_can_move_in_one_step():
    # A boundary is moved by writing both sides of it. One call proposing both
    # is the shape that makes that possible without a merge choosing between
    # two halves of the same intent.
    client = Recorder(analyst={
        "batch_size": 4,
        "patch": {
            "reasoning": "move the boundary",
            "edits": [
                {
                    "op": "replace",
                    "path": "billing/SKILL.md",
                    "target": "description: Invoices, credit notes and outstanding balances.",
                    "content": "description: Refunds and payment failures.",
                },
                {
                    "op": "replace",
                    "path": "reporting/SKILL.md",
                    "target": "description: Monthly revenue reports.",
                    "content": "description: Revenue reports and billing statements.",
                },
            ],
        },
    })
    outcome = run(client=client)
    assert outcome.n_edits_applied == 2
    assert "Refunds and payment failures." in outcome.files["billing/SKILL.md"]
    assert "Revenue reports and billing statements." in outcome.files["reporting/SKILL.md"]


def test_routing_still_cannot_edit_a_body():
    client = Recorder(analyst={
        "batch_size": 4,
        "patch": {
            "reasoning": "sneak into the body",
            "edits": [{"op": "append", "path": "billing/SKILL.md", "content": "\n2. New rule.\n"}],
        },
    })
    outcome = run(client=client)
    assert "New rule." not in outcome.files["billing/SKILL.md"]
    assert outcome.n_edits_applied == 0


def test_an_analyst_naming_something_outside_the_descriptions_is_recorded():
    # The observation that routing cannot be fixed by any description — the
    # system prompt forbids opening skills, say — used to die inside
    # `patch.reasoning`. It is the one thing a run needs surfaced, because the
    # symptom otherwise is twenty steps of "0 edits applied".
    client = Recorder(analyst={
        "batch_size": 4,
        "routing_blocked_by": "the system prompt tells the agent to answer directly",
        "patch": {"reasoning": "nothing here is the description's fault", "edits": []},
    })
    outcome = run(client=client)
    assert "system prompt" in (outcome.routing_blocked_by or "")


def test_nothing_blocking_leaves_the_field_empty_rather_than_inventing_a_reason():
    outcome = run()
    assert not outcome.routing_blocked_by
