"""What a routing analyst is shown about the skills it is competing with.

`routing` mode optimises one description against the others: the whole judgement
is comparative, and both routing prompts say so out loud — "a workspace
containing SEVERAL skills … the others are shown so you can see what it is
competing with", then "Distinguish it from the *other* descriptions you were
shown."

Nothing was shown. `run_update_stage` receives `files=state.current_files`,
which is the target skill's directory and nothing else; `workspace_baseline` —
every other skill, pinned at run start — went to the agent on every rollout and
never to the analyst. The model was being asked to differentiate a description
from competitors it had never seen, and the prompt told it that it had. What
comes back from that is a description that *sounds* discriminating, written
against imagined alternatives.

Two things are therefore worth pinning here, and the second is the one that
bites later:

  * routing sees the competitors, isolated sees exactly what it saw before;
  * only their **descriptions** travel. Bodies are the bulk of a workspace and
    are irrelevant to a routing decision — the description is the entire basis
    on which the agent chooses — and the trajectory budget (`reflect_budget_chars`)
    deliberately does not cover the skill section, so anything added here lands
    in the unbudgeted part of the prompt. A workspace of two hundred skills must
    not be able to push the analyst call over the model's context window, which
    truncates nothing and simply loses the step its gradient.
"""
from __future__ import annotations

import json
import threading

from app.optimizer import skillio
from app.optimizer.analyst import build_user_prompt
from app.optimizer.trajectory import Trajectory, Turn
from app.optimizer.update import run_update_stage

SKILL_DIR = "billing"

FILES = {
    "billing/SKILL.md": (
        "---\n"
        "name: billing\n"
        "description: Invoices, credit notes and outstanding balances.\n"
        "---\n"
        "\n"
        "# Billing\n"
        "\n"
        "## Rules\n"
        "1. Quote figures in the account's own currency.\n"
    ),
    "billing/references/refunds.md": "# Refunds\n\nRefunds settle in 5 days.\n",
}

BASELINE = {
    "reporting/SKILL.md": (
        "---\n"
        "name: reporting\n"
        "description: Monthly revenue reports and period-on-period comparisons.\n"
        "---\n"
        "# Reporting\n"
        "UNIQUE_REPORTING_BODY_SENTENCE that must never reach the analyst.\n"
    ),
    "reporting/references/periods.md": "# Periods\nUNIQUE_REFERENCE_SENTENCE.\n",
    "shipping/SKILL.md": (
        "---\n"
        "name: shipping\n"
        "description: Delivery status, carriers and tracking numbers.\n"
        "---\n"
        "# Shipping\nUNIQUE_SHIPPING_BODY_SENTENCE.\n"
    ),
}


class ScriptedOptimizer:
    model_name = "scripted"

    def __init__(self):
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def chat_optimizer(self, system, user, max_completion_tokens=16384,
                       retries=3, stage="optimizer", timeout=None):
        with self._lock:
            self.calls.append({"stage": stage, "system": system, "user": user})
        usage = {"calls": 1, "prompt_tokens": 10, "completion_tokens": 5}
        if stage == "ranking":
            return json.dumps({"selected_indices": []}), usage
        if stage == "merge":
            return json.dumps({"reasoning": "merged", "edits": []}), usage
        return json.dumps({"batch_size": 1, "patch": {"reasoning": "", "edits": []}}), usage

    def prompts(self, stage: str) -> list[str]:
        return [c["user"] for c in self.calls if c["stage"] == stage]


def items(n=2):
    return [
        {
            "id": f"q_{i}",
            "hard": 0.0,
            "soft": 0.0,
            "task_description": f"question {i}",
            "reference_text": "the gold answer",
            "agent_response": "an answer",
            "fail_reason": "wrong",
            "n_turns": 1,
            "trajectory": Trajectory(turns=[Turn(role="assistant", text="an answer")]),
        }
        for i in range(n)
    ]


def run(**overrides):
    kwargs = dict(
        files=FILES,
        skill_dir=SKILL_DIR,
        mode="isolated",
        items=items(),
        client=ScriptedOptimizer(),
        edit_budget=4,
        minibatch_size=8,
        analyst_workers=1,
        merge_batch_size=8,
        seed=7,
    )
    kwargs.update(overrides)
    outcome = run_update_stage(**kwargs)
    return kwargs["client"], outcome


# --- Rendering the competitors ----------------------------------------------


def test_competing_skills_are_listed_by_name_and_description():
    block = skillio.render_competing_skills(BASELINE)

    assert "reporting" in block and "shipping" in block
    assert "Monthly revenue reports" in block
    assert "Delivery status, carriers and tracking numbers." in block


def test_only_descriptions_travel_never_bodies():
    """The bulk of a workspace is bodies, and none of it informs a routing choice."""
    block = skillio.render_competing_skills(BASELINE)

    assert "UNIQUE_REPORTING_BODY_SENTENCE" not in block
    assert "UNIQUE_SHIPPING_BODY_SENTENCE" not in block
    assert "UNIQUE_REFERENCE_SENTENCE" not in block
    assert "## Rules" not in block


def test_a_skill_without_a_description_is_still_named():
    """Silence about it would read as "no such skill" to the analyst.

    A competitor with no description still competes — the agent can open it —
    and the analyst's job is to distinguish this skill from the ones on offer.
    Omitting it entirely hides a real alternative; naming it says what is known.
    """
    block = skillio.render_competing_skills({
        "legacy/SKILL.md": "# Legacy\nNo frontmatter at all.\n",
    })

    assert "legacy" in block
    assert "No frontmatter at all." not in block


def test_an_empty_workspace_renders_nothing():
    """No competitors is a real state, and an empty heading would be a lie."""
    assert skillio.render_competing_skills({}) == ""


def test_the_block_is_capped_and_says_what_it_left_out():
    """The skill section is outside the trajectory budget, so this needs its own.

    Going over the optimizer model's context window truncates nothing: the call
    is refused and the step loses that minibatch's gradient entirely. A silent
    cap would be almost as bad — the analyst would compare against a subset
    while believing it had the set — so the omission is stated in the block.
    """
    many = {
        f"skill_{i:03d}/SKILL.md": (
            f"---\nname: skill_{i:03d}\ndescription: {'x' * 300}\n---\n# Body\n"
        )
        for i in range(200)
    }
    block = skillio.render_competing_skills(many, budget_chars=4000)

    assert len(block) <= 4000
    assert "more" in block.lower(), "it must say that it left some out"


def test_an_uncapped_small_workspace_says_nothing_about_omissions():
    block = skillio.render_competing_skills(BASELINE, budget_chars=4000)
    assert "more" not in block.lower()


# --- Where it lands in the prompt -------------------------------------------


def test_the_user_prompt_carries_the_competitors_after_the_current_skill():
    prompt = build_user_prompt(
        "SKILL BODY HERE", items(1), source_type="failure", edit_budget=2,
        mode="patch", competing_skills="## Competing Skills\n- reporting: Revenue.",
    )

    assert "## Current Skill" in prompt
    assert "## Competing Skills" in prompt
    assert prompt.index("## Current Skill") < prompt.index("## Competing Skills")


def test_the_user_prompt_is_unchanged_when_there_are_no_competitors():
    """Isolated must produce exactly the prompt it produced before this existed."""
    before = build_user_prompt(
        "SKILL BODY HERE", items(1), source_type="failure", edit_budget=2, mode="patch",
    )
    with_empty = build_user_prompt(
        "SKILL BODY HERE", items(1), source_type="failure", edit_budget=2,
        mode="patch", competing_skills="",
    )

    assert before == with_empty
    assert "Competing" not in before


# --- End to end through the update stage ------------------------------------


def test_a_routing_run_shows_the_analyst_what_it_competes_with():
    client, _ = run(mode="routing", context_files=BASELINE)

    analyst = client.prompts("analyst")
    assert analyst, "the analyst was called"
    assert all("Monthly revenue reports" in p for p in analyst)
    assert all("Delivery status" in p for p in analyst)


def test_a_routing_run_never_sends_a_competitor_body():
    client, _ = run(mode="routing", context_files=BASELINE)

    for prompt in client.prompts("analyst"):
        assert "UNIQUE_REPORTING_BODY_SENTENCE" not in prompt
        assert "UNIQUE_SHIPPING_BODY_SENTENCE" not in prompt


def test_an_isolated_run_is_told_nothing_about_other_skills():
    """Isolated sends one skill to the agent; there is no choice to inform.

    Passing context here would not merely be useless, it would be misleading —
    the analyst would weigh alternatives the agent was never offered.
    """
    client, _ = run(mode="isolated", context_files=BASELINE)

    for prompt in client.prompts("analyst"):
        assert "Competing" not in prompt
        assert "Monthly revenue reports" not in prompt


def test_merge_and_ranking_are_not_given_the_competitors():
    """They combine and choose among edits; neither makes a routing judgement.

    Both prompts open with the same "## Current Skill" section, so it would have
    been easy to fold the block in there and pay for it three times per step on
    the most expensive model in the run.
    """
    client, _ = run(mode="routing", context_files=BASELINE, items=items(2))

    for stage in ("merge", "ranking"):
        for prompt in client.prompts(stage):
            assert "Monthly revenue reports" not in prompt


def test_the_target_skill_is_never_listed_among_its_own_competitors():
    """`workspace_baseline` excludes it already; a caller passing everything must
    not produce a prompt telling the analyst to differentiate it from itself."""
    client, _ = run(mode="routing", context_files={**BASELINE, **FILES})

    for prompt in client.prompts("analyst"):
        head, _, tail = prompt.partition("## Competing Skills")
        assert "billing" not in tail.split("## ")[0], tail[:400]


def test_a_routing_run_without_context_still_works():
    """A run resumed from before this existed passes nothing; it must not crash."""
    client, outcome = run(mode="routing", context_files=None)

    assert client.prompts("analyst")
    assert outcome.files == FILES


# --- Bounding the skill section ----------------------------------------------
#
# `render_competing_skills` has a budget because the skill section sits in front
# of `reflect_budget_chars` and is unbudgeted, and going over the optimizer
# model's context window truncates nothing — the call is refused and the step
# loses its gradient. `render_skill` is the *larger* half of that same section
# and had no budget at all.
#
# It did not matter while a target was one skill. Routing takes several, the
# wizard ticks every usable skill by default, and `workspace_baseline` is the
# workspace minus the targets — so on the default path the competing block is
# empty and every body of every skill in the workspace goes through
# `render_skill` instead, in full, on the analyst call and again on each merge
# and the ranking call. The one part with a cap contributes nothing and the part
# without one carries everything.


def wide_workspace(n=40, body_chars=4000):
    return {
        f"skill_{i:03d}/SKILL.md": (
            f"---\nname: skill_{i:03d}\ndescription: Skill {i:03d} does things.\n---\n"
            f"# Body {i:03d}\n" + ("filler line\n" * (body_chars // 12))
        )
        for i in range(n)
    }


def test_render_skill_is_unbounded_by_default():
    """Isolated edits the body, so it must see the body. Nothing changes there."""
    rendered = skillio.render_skill(FILES, SKILL_DIR)

    assert "Quote figures in the account's own currency." in rendered
    assert "Refunds settle in 5 days." in rendered


def test_a_budget_keeps_every_targets_frontmatter_whole():
    """The description is the one thing routing edits, and an edit must name its
    exact current text to land. Truncating a frontmatter would ask the analyst to
    target text it was never shown."""
    files = wide_workspace()
    dirs = sorted(p.split("/", 1)[0] for p in files)
    rendered = skillio.render_skill(files, dirs, budget_chars=20_000)

    assert len(rendered) <= 20_000
    for i in range(len(dirs)):
        assert f"description: Skill {i:03d} does things." in rendered


def test_a_budget_says_how_much_it_dropped():
    """A silent cap is the same bug one level down: the analyst would believe it
    had been shown a skill in full and target a line that never arrived."""
    files = wide_workspace()
    dirs = sorted(p.split("/", 1)[0] for p in files)
    rendered = skillio.render_skill(files, dirs, budget_chars=20_000)

    assert "not shown" in rendered.lower()


def test_a_workspace_that_fits_is_rendered_whole_and_says_nothing():
    files = wide_workspace(n=2, body_chars=200)
    dirs = sorted(p.split("/", 1)[0] for p in files)
    rendered = skillio.render_skill(files, dirs, budget_chars=100_000)

    assert "not shown" not in rendered.lower()
    assert rendered == skillio.render_skill(files, dirs)


def test_reference_files_are_dropped_before_an_entry_point_body():
    """Order of sacrifice: the entry points are what the agent reads first and
    what this mode edits, so a reference file goes before any of them is cut."""
    files = {
        "billing/SKILL.md": "---\nname: billing\ndescription: Invoices.\n---\n# Billing\nKEEP_THIS_BODY\n",
        "reporting/SKILL.md": "---\nname: reporting\ndescription: Reports.\n---\n# Reporting\nKEEP_THIS_TOO\n",
        "billing/references/long.md": "DROP_ME\n" + ("x" * 50_000),
    }
    rendered = skillio.render_skill(
        files, ["billing", "reporting"], budget_chars=2_000
    )

    assert "KEEP_THIS_BODY" in rendered
    assert "KEEP_THIS_TOO" in rendered
    assert "DROP_ME" not in rendered
