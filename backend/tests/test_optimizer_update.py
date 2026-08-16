"""The update stage: trajectories in, a candidate skill out.

This is the half of a step that SkillOpt owns — reflect over minibatches,
aggregate the patches, clip to the learning rate, apply — and what is tested
here is the seam our code holds around it: that the right trajectories reach the
right analyst call, that what we *record* about a call is what was actually
sent, and that the mode's protection survives all the way to the applied file.

The recording matters as much as the algorithm. The Part 1 page shows the
optimizer's own prompt and its raw answer, and that display is the only way a
developer can tell "the model had a bad idea" from "the model never saw the
failing trajectory". A record reconstructed after the fact, rather than captured
at the call, is a display that agrees with itself and disagrees with reality.
"""
from __future__ import annotations

import json
import re
import threading

import pytest

from app.optimizer.trajectory import ToolCall, Trajectory, Turn
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


class ScriptedOptimizer:
    """An `OptimizerClient` whose answer per stage the test dictates.

    Records every call, with the thread it arrived on, so the tests can check
    both the content of a prompt and that parallel analyst calls did not get
    their trajectories crossed.
    """

    model_name = "scripted"

    def __init__(self, *, analyst=None, merge=None, ranking=None, fail_on=None):
        self._analyst = analyst
        self._merge = merge
        self._ranking = ranking
        self._fail_on = fail_on or set()
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def chat_optimizer(self, system, user, max_completion_tokens=16384,
                       retries=3, stage="optimizer", timeout=None):
        with self._lock:
            self.calls.append({"stage": stage, "system": system, "user": user})
        if stage in self._fail_on:
            raise RuntimeError(f"scripted failure in {stage}")
        usage = {"calls": 1, "prompt_tokens": 10, "completion_tokens": 5}
        if stage == "ranking":
            return json.dumps(self._ranking or {"selected_indices": [0]}), usage
        if stage == "merge":
            return json.dumps(self._merge or {"reasoning": "merged", "edits": []}), usage
        body = self._analyst
        if callable(body):
            body = body(user)
        return json.dumps(body or {"batch_size": 1, "patch": {"reasoning": "", "edits": []}}), usage

    def analyst_prompts(self) -> list[str]:
        return [c["user"] for c in self.calls if c["stage"] == "analyst"]


def edit(op="append", path="billing/SKILL.md", **kw) -> dict:
    return {"op": op, "path": path, **kw}


def analyst_reply(edits, *, reasoning="because") -> dict:
    return {
        "batch_size": 2,
        "failure_summary": [{"failure_type": "rule_missing", "count": 2, "description": "d"}],
        "patch": {"reasoning": reasoning, "edits": edits},
    }


def items(n, *, correct=False, prefix="q") -> list[dict]:
    """`n` analyst items, each with a one-turn trajectory naming itself."""
    return [
        {
            "id": f"{prefix}_{i}",
            "hard": 1.0 if correct else 0.0,
            "soft": 1.0 if correct else 0.0,
            "task_description": f"question {prefix}_{i}",
            "reference_text": "the gold answer",
            "agent_response": f"answer for {prefix}_{i}",
            "fail_reason": "" if correct else "the figure is wrong",
            "n_turns": 1,
            "trajectory": Trajectory(
                turns=[Turn(role="assistant", text=f"answer for {prefix}_{i}")],
            ),
        }
        for i in range(n)
    ]


def run(**overrides):
    kwargs = dict(
        files=FILES,
        skill_dir=SKILL_DIR,
        mode="isolated",
        items=items(2),
        client=ScriptedOptimizer(),
        edit_budget=4,
        minibatch_size=8,
        analyst_workers=2,
        merge_batch_size=8,
        seed=7,
    )
    kwargs.update(overrides)
    return run_update_stage(**kwargs)


# --- How trajectories are grouped ------------------------------------------


def test_failures_are_split_into_minibatches_of_the_configured_size():
    """One analyst call per minibatch — the whole point of minibatch reflect.

    If the splitting silently collapsed to one call, every step would send the
    entire batch in one prompt: the context would grow with the training set,
    and the 'gradient' would be a single opinion rather than several to
    aggregate.
    """
    client = ScriptedOptimizer(analyst=analyst_reply([]))
    outcome = run(items=items(9), minibatch_size=4, client=client)
    failure_batches = [m for m in outcome.minibatches if m.source_type == "failure"]
    assert [m.n_items for m in failure_batches] == [4, 4, 1]
    assert len(client.analyst_prompts()) == 3


def test_successes_and_failures_go_to_different_analysts():
    """They ask opposite questions, and mixing them wastes both.

    The failure analyst is told to find what went wrong; the success analyst is
    told to find what is worth keeping. A batch containing both produces
    reflections about neither.
    """
    client = ScriptedOptimizer(analyst=analyst_reply([]))
    outcome = run(items=items(3) + items(2, correct=True, prefix="ok"), client=client)
    kinds = sorted(m.source_type for m in outcome.minibatches)
    assert kinds == ["failure", "success"]
    failure = next(m for m in outcome.minibatches if m.source_type == "failure")
    success = next(m for m in outcome.minibatches if m.source_type == "success")
    assert failure.n_items == 3
    assert success.n_items == 2


def test_failure_only_skips_the_success_analyst_entirely():
    """A configured saving must actually save the call.

    `failure_only` exists to halve the optimizer bill on runs where success
    patterns are not paying for themselves. If the call still went out and its
    patch were merely discarded, the setting would cost exactly as much while
    appearing to work.
    """
    client = ScriptedOptimizer(analyst=analyst_reply([]))
    outcome = run(
        items=items(2) + items(2, correct=True, prefix="ok"),
        failure_only=True, client=client,
    )
    assert all(m.source_type == "failure" for m in outcome.minibatches)
    assert len(client.analyst_prompts()) == 1


def test_every_item_reaches_exactly_one_minibatch():
    """No trajectory may be dropped or double-counted by the shuffle.

    The shuffle is seeded so that a resumed run reproduces the same grouping.
    A shuffle that lost an item would be invisible — the step would still
    produce a patch, from evidence that was quietly one question short.
    """
    outcome = run(items=items(7), minibatch_size=3)
    seen = [key for m in outcome.minibatches for key in m.item_keys]
    assert sorted(seen) == sorted(f"q_{i}" for i in range(7))


# --- What is recorded about a call -----------------------------------------


def test_the_recorded_prompt_is_the_one_that_was_sent():
    """Captured at the seam, never rebuilt afterwards.

    A rebuilt prompt drifts from the real one the moment either the vendored
    formatter or our own copy of it changes, and the Part 1 page would then be
    showing a plausible fiction. Captured means the page can only ever be wrong
    in the same way the run was.
    """
    client = ScriptedOptimizer(analyst=analyst_reply([]))
    outcome = run(items=items(2), client=client)
    record = outcome.minibatches[0]
    sent = next(c for c in client.calls if c["stage"] == "analyst")
    assert record.prompt_user == sent["user"]
    assert record.prompt_system == sent["system"]


def test_parallel_analyst_calls_are_attributed_to_their_own_minibatch():
    """Two calls in flight must not swap prompts on the way into the record.

    The analysts run on a thread pool, so completion order is not submission
    order. Attributing by arrival would pair minibatch 1's record with
    minibatch 2's prompt about half the time — and the record would still look
    entirely reasonable.
    """
    client = ScriptedOptimizer(analyst=analyst_reply([]))
    outcome = run(items=items(6), minibatch_size=2, analyst_workers=3, client=client)
    for record in outcome.minibatches:
        ids_in_prompt = set(re.findall(r"\(id=([^)]+)\)", record.prompt_user))
        assert ids_in_prompt == set(record.item_keys)


def test_the_analyst_raw_answer_is_kept_for_display():
    """The failure summary shown on Part 1 comes from here, not from a re-parse."""
    client = ScriptedOptimizer(analyst=analyst_reply([edit(content="x")]))
    outcome = run(client=client)
    raw = outcome.minibatches[0].raw_output
    assert raw["failure_summary"][0]["failure_type"] == "rule_missing"


def test_an_analyst_that_raises_is_recorded_and_the_step_continues():
    """One dead minibatch must not cost the step its other gradients.

    Upstream swallows the exception and returns no patch, which on its own makes
    a transient outage look like 'the model had nothing to say'. Recording the
    error is what separates those two, and continuing is what keeps a five-batch
    step from being thrown away over one failure.
    """
    client = ScriptedOptimizer(analyst=analyst_reply([edit(content="x")]), fail_on={"analyst"})
    outcome = run(items=items(4), minibatch_size=2, client=client)
    assert len(outcome.minibatches) == 2
    assert all(m.error for m in outcome.minibatches)
    assert outcome.files == FILES


def test_token_usage_is_summed_across_every_stage():
    """The cost of a step is the whole step, not the analyst calls alone.

    Merge and ranking are LLM calls too, on prompts that carry the entire skill.
    A total that counted only the analysts would understate a run's bill by the
    part that scales with skill size.
    """
    client = ScriptedOptimizer(
        analyst=analyst_reply([edit(content=f"line {i}") for i in range(3)]),
        merge={"reasoning": "m", "edits": [edit(content=f"line {i}") for i in range(6)]},
        ranking={"selected_indices": [0, 1]},
    )
    outcome = run(items=items(4), minibatch_size=2, edit_budget=2, client=client)
    assert outcome.tokens["calls"] == len(client.calls)
    assert outcome.tokens["prompt_tokens"] == 10 * len(client.calls)


def test_the_truncation_ledger_travels_with_the_minibatch_that_was_truncated():
    """Part 1 says 'these trajectories were shortened' beside the prompt.

    The ledger is per item; the display is per minibatch. If they were not
    matched here, the page would warn about truncation on batches that had none
    and stay silent on the ones that did.
    """
    ledger = {"q_0": [{"item_key": "q_0", "span_index": 2, "field": "output",
                       "before": 900, "after": 400, "stage": 1}]}
    outcome = run(items=items(2), truncation_by_item=ledger)
    record = outcome.minibatches[0]
    assert [entry["item_key"] for entry in record.truncation] == ["q_0"]
    assert record.chars_after < record.chars_before


# --- The edit budget --------------------------------------------------------


def test_each_analyst_patch_is_truncated_to_the_edit_budget():
    """The learning rate has to bind before aggregation, not only after.

    Otherwise a step with six minibatches arrives at the merge with six times
    the budget in edits, and the merge — an LLM — decides how much of the
    learning rate to respect.
    """
    client = ScriptedOptimizer(
        analyst=analyst_reply([edit(content=f"rule {i}") for i in range(6)]),
        merge={"reasoning": "m", "edits": [edit(content="rule 0")]},
    )
    outcome = run(items=items(2), edit_budget=2, client=client)
    assert len(outcome.minibatches[0].raw_output["patch"]["edits"]) == 2


def test_ranking_is_skipped_when_the_merged_pool_already_fits():
    """An LLM call that cannot change the answer should not be made.

    Ranking exists to choose *which* edits to drop. With nothing to drop it is
    a prompt carrying the whole skill, for a result that is already known.
    """
    client = ScriptedOptimizer(
        analyst=analyst_reply([edit(content="one")]),
        merge={"reasoning": "m", "edits": [edit(content="one")]},
    )
    run(edit_budget=4, client=client)
    assert not any(c["stage"] == "ranking" for c in client.calls)


def test_ranking_clips_an_oversized_pool_to_the_budget():
    """Two minibatches, so aggregation really runs and can overshoot the budget.

    This is the shape the learning rate exists for: independent analysts each
    proposing within budget, whose union is over it.
    """
    client = ScriptedOptimizer(
        analyst=analyst_reply([edit(content=f"r{i}") for i in range(2)]),
        merge={"reasoning": "m", "edits": [edit(content=f"r{i}") for i in range(4)]},
        ranking={"selected_indices": [3, 0]},
    )
    outcome = run(items=items(4), minibatch_size=2, edit_budget=2, client=client)
    assert outcome.n_edits_merged == 4
    assert outcome.n_edits_ranked == 2
    assert any(c["stage"] == "ranking" for c in client.calls)


# --- Applying, and what the mode protects -----------------------------------


def test_an_applied_edit_reaches_the_named_file_and_only_that_file():
    client = ScriptedOptimizer(
        analyst=analyst_reply([edit(path="billing/references/refunds.md", content="Note.")]),
        merge={"reasoning": "m",
               "edits": [edit(path="billing/references/refunds.md", content="Note.")]},
    )
    outcome = run(client=client)
    assert "Note." in outcome.files["billing/references/refunds.md"]
    assert outcome.files["billing/SKILL.md"] == FILES["billing/SKILL.md"]
    assert outcome.n_edits_applied == 1
    assert outcome.n_edits_skipped == 0


def test_a_skipped_edit_is_counted_as_skipped_rather_than_applied():
    """'The idea was bad' and 'the target string had a typo' are different bugs.

    Part 2 lists the edits that did not land. If a skipped edit were counted
    among the applied, the step would report changes the file does not contain
    and the diff would silently disagree with the count above it.
    """
    client = ScriptedOptimizer(
        analyst=analyst_reply([edit(op="replace", target="nowhere at all", content="x")]),
        merge={"reasoning": "m",
               "edits": [edit(op="replace", target="nowhere at all", content="x")]},
    )
    outcome = run(client=client)
    assert outcome.n_edits_applied == 0
    assert outcome.n_edits_skipped == 1
    assert outcome.files == FILES
    assert outcome.reports[0]["status"].startswith("skipped")


def test_routing_mode_cannot_edit_the_body():
    """The mode's protection has to hold at the point of application.

    A routing run is judged by an activation guard that only makes sense if the
    body is fixed. If a body edit slipped through, the run would be measuring a
    body change against a routing criterion — and the two modes would stop being
    comparable to each other or to themselves.
    """
    client = ScriptedOptimizer(
        analyst=analyst_reply([edit(content="a new rule in the body")]),
        merge={"reasoning": "m", "edits": [edit(content="a new rule in the body")]},
    )
    outcome = run(mode="routing", client=client)
    assert outcome.files["billing/SKILL.md"] == FILES["billing/SKILL.md"]
    assert outcome.n_edits_applied == 0


def test_isolated_mode_cannot_edit_the_frontmatter():
    client = ScriptedOptimizer(
        analyst=analyst_reply([edit(
            op="replace",
            target="description: Invoices, credit notes and outstanding balances.",
            content="description: everything about money",
        )]),
        merge={"reasoning": "m", "edits": [edit(
            op="replace",
            target="description: Invoices, credit notes and outstanding balances.",
            content="description: everything about money",
        )]},
    )
    outcome = run(mode="isolated", client=client)
    assert "everything about money" not in outcome.files["billing/SKILL.md"]


def test_a_path_that_escapes_the_skill_directory_is_refused():
    """The paths come from a language model and are therefore untrusted input.

    Nothing here needs to write outside the skill, so a path that tries is
    either a confused model or a prompt-injected one. Either way the run has no
    business acting on it.
    """
    client = ScriptedOptimizer(
        analyst=analyst_reply([edit(path="../../etc/passwd", content="x")]),
        merge={"reasoning": "m", "edits": [edit(path="../../etc/passwd", content="x")]},
    )
    outcome = run(client=client)
    assert set(outcome.files) == set(FILES)
    assert outcome.reports[0]["status"] == "skipped_invalid_path"


def test_a_candidate_with_nothing_applied_is_byte_identical_to_its_parent():
    """This is what makes the validation-score cache correct.

    The engine skips a whole validation rollout when the candidate hashes to
    something it has already scored. That is only sound if 'no edit landed'
    produces exactly the same bytes — a stray trailing newline would send the
    run off to spend a full split re-measuring the skill it is already running.
    """
    client = ScriptedOptimizer(analyst=analyst_reply([]),
                               merge={"reasoning": "m", "edits": []})
    outcome = run(client=client)
    assert outcome.files == FILES


def test_the_edit_summary_comes_from_the_patch_that_was_actually_applied():
    """The tooltip's second half is the optimizer's own account of the step.

    It has to be the *selected* patch's reasoning, not the first analyst's: by
    the time a step has several minibatches, the merge is where the reasoning
    about the step as a whole is written, and quoting one analyst would describe
    a fraction of the edits as though it described all of them.
    """
    client = ScriptedOptimizer(
        analyst=analyst_reply([edit(content="x")], reasoning="one batch's view"),
        merge={"reasoning": "collapsed two near-duplicate rules",
               "edits": [edit(content="x")]},
    )
    outcome = run(items=items(4), minibatch_size=2, client=client)
    assert outcome.edit_summary == "collapsed two near-duplicate rules"


def test_no_trajectories_means_no_optimizer_calls_at_all():
    """A step whose entire batch failed has nothing to reflect on.

    Sending an empty minibatch would bill for a prompt containing no evidence
    and invite the model to invent a reason to edit the skill.
    """
    client = ScriptedOptimizer()
    outcome = run(items=[], client=client)
    assert client.calls == []
    assert outcome.files == FILES
    assert outcome.minibatches == []


def test_the_mode_chooses_the_analyst_prompt():
    """Two modes, two different questions to ask.

    Routing gets a prompt about descriptions and competitors; isolated gets one
    about the body. Falling back to the generic prompt would ask a routing run
    to rewrite content it is forbidden to touch, and every edit would be
    discarded at application time for reasons the model was never told.
    """
    isolated_client = ScriptedOptimizer(analyst=analyst_reply([]))
    routing_client = ScriptedOptimizer(analyst=analyst_reply([]))
    run(mode="isolated", client=isolated_client)
    run(mode="routing", client=routing_client)
    isolated_system = isolated_client.calls[0]["system"]
    routing_system = routing_client.calls[0]["system"]
    assert isolated_system != routing_system
    assert "description" in routing_system.lower()


# --- What the truncation figures actually mean ------------------------------
#
# Added in Phase 6, when Part 1 started printing these two numbers side by side
# as "41,200 → 12,000 chars" and a real run produced 24,165 → 130,972. They were
# measuring different things: the "after" was the whole batch, the "before" was
# only the slots that had been cut.


def _spoken_items(n, *, chars=500):
    """Items carrying a trajectory big enough for the size figures to be visible."""
    return [
        {
            "id": f"q_{i}",
            "hard": 0.0,
            "soft": 0.0,
            "task_description": f"question {i}",
            "reference_text": "the gold answer",
            "agent_response": "an answer",
            "n_turns": 2,
            "trajectory": Trajectory(
                turns=[
                    Turn(role="assistant", tool_calls=[ToolCall(name="search", args="invoices")]),
                    Turn(role="tool", text="x" * chars),
                ],
            ),
        }
        for i in range(n)
    ]


def test_the_before_and_after_sizes_describe_the_same_batch():
    """They are rendered as one arrow, so they have to measure one thing.

    `chars_after` is the size of the batch as the analyst received it. The
    matching "before" is therefore the size that batch *would* have been without
    truncation — not the sum of the original sizes of the cut slots, which
    counts only the parts that were touched and leaves out everything that was
    not. Those two are unrelated quantities, and on a batch whose untouched
    turns outweigh its cut ones the "before" comes out smaller than the "after":
    an arrow pointing the wrong way, over a number that claims to show how much
    evidence was lost.
    """
    ledger = {
        "q_0": [{"item_key": "q_0", "span_index": 2, "field": "obs",
                 "before": 900, "after": 400, "stage": 1}],
    }
    record = run(items=_spoken_items(2, chars=500), truncation_by_item=ledger).minibatches[0]

    assert record.chars_before > record.chars_after
    # Exactly the 500 characters the ledger says were cut, no more and no less.
    assert record.chars_before - record.chars_after == 500


def test_a_batch_that_was_not_truncated_reports_no_change(): 
    """Equal, not zero and not absent.

    "Nothing was truncated" is the reassuring case, and it is only reassuring if
    it is distinguishable from "we did not measure". The page prints the pair
    either way.
    """
    record = run(items=_spoken_items(2, chars=500), truncation_by_item={}).minibatches[0]
    assert record.chars_before == record.chars_after
    assert record.chars_after > 0
    assert record.truncation == []
