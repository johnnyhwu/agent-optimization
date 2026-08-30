"""The training loop: what happens across steps, and what survives an interruption.

An optimization run is an hour-long loop that spends real money and ends by
handing someone a skill they will put on an agent. Almost everything worth
protecting about it is control flow rather than arithmetic — which skill was in
the agent's hands for this rollout, what a rejected step rolls back to, what a
resumed run does *not* re-run, whether the stream ever closes.

None of that needs a database, which is why the engine talks to
`OptimizationStore` rather than to SQLAlchemy: the store here is a recorder, and
every assertion below reads what the loop tried to persist. `tests/
test_orchestrator.py` set this pattern for the eval run; this is the same idea
one level up.

The rollout and update stages are stubbed by default. Both have their own suites
(`test_optimizer_scoring.py`, `test_optimizer_update.py`) and re-testing them
through the loop would only make these tests slower and less specific. One test
at the bottom runs the whole thing on the fake seams, end to end, because a loop
whose parts all work individually can still be wired up wrong.
"""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from app.optimizer import engine
from app.optimizer.store import Item, ResultRow, RolloutSummary, ResumeState, RunSpec
from app.optimizer.update import MinibatchRecord, UpdateOutcome
from app.optimizer.vendor.slow_update import SLOW_UPDATE_END, SLOW_UPDATE_START

SKILL = {"billing/SKILL.md": "# Billing\n\n1. Quote the currency.\n"}


# --- Doubles ----------------------------------------------------------------


class RecordingStore:
    """An `OptimizationStore` that keeps everything instead of writing it."""

    def __init__(self, spec: RunSpec, train, val, resume=None):
        self.spec = spec
        self._items = {"train": train, "val": val}
        self._resume = resume
        self.steps: list[dict] = []
        self.rollouts: list[tuple[int, RolloutSummary]] = []
        self.minibatches: list[dict] = []
        self.stage_calls: list[dict] = []
        self.skills: list[dict] = []
        self.run_updates: list[dict] = []
        self.cancel = False

    # reads
    async def load_run(self, run_id):
        return self.spec

    async def load_items(self, run_id, split):
        return list(self._items[split])

    async def last_completed_step(self, run_id):
        return None if self._resume is None else self._resume.last_step_no

    async def load_resume_state(self, run_id):
        return self._resume

    async def cancel_requested(self, run_id):
        return self.cancel

    async def load_val_results(self, run_id, step_no):
        """Per-item validation results for one step, as the slow update needs them.

        Read back from storage rather than kept in memory so that a resumed run
        can still compare across an epoch boundary it did not itself execute.
        """
        for recorded_step, summary in self.rollouts:
            if recorded_step == step_no and summary.split == "val":
                return [
                    {
                        "id": row.item_key,
                        "hard": 1 if row.verdict == "correct" else 0,
                        "soft": float(row.judge_score or 0.0),
                        "predicted_answer": row.agent_response or "",
                        "fail_reason": row.judge_comment or "",
                    }
                    for row in summary.results
                ]
        return []

    # writes
    async def start_step(self, run_id, *, step_no, epoch_no, step_in_epoch, parent_step_no):
        step = {
            "id": uuid.uuid4(), "step_no": step_no, "epoch_no": epoch_no,
            "step_in_epoch": step_in_epoch, "parent_step_no": parent_step_no,
        }
        self.steps.append(step)
        return step["id"]

    async def record_rollout(self, step_id, summary):
        self.rollouts.append((self._step_no(step_id), summary))
        return uuid.uuid4()

    async def record_minibatch(self, step_id, **fields):
        self.minibatches.append({"step_no": self._step_no(step_id), **fields})

    async def record_stage_call(self, step_id, **fields):
        self.stage_calls.append({"step_no": self._step_no(step_id), **fields})

    async def record_skill(self, run_id, *, step_no, kind, files, content_hash, per_file_stats):
        self.skills.append({"step_no": step_no, "kind": kind, "files": files,
                            "content_hash": content_hash})

    async def finish_step(self, step_id, **fields):
        self._step(step_id).update(fields)

    async def set_status(self, run_id, status, **fields):
        self.run_updates.append({"status": status, **fields})

    async def finish_run(self, run_id, **fields):
        self.run_updates.append(dict(fields))

    # helpers for the assertions
    def _step(self, step_id):
        return next(s for s in self.steps if s["id"] == step_id)

    def _step_no(self, step_id):
        return self._step(step_id)["step_no"]

    def step(self, step_no):
        return next(s for s in self.steps if s["step_no"] == step_no)

    def rollout(self, step_no, split):
        return next(s for n, s in self.rollouts if n == step_no and s.split == split)

    def splits_of(self, step_no):
        return [s.split for n, s in self.rollouts if n == step_no]

    @property
    def final(self):
        return self.run_updates[-1]


def make_spec(**overrides) -> RunSpec:
    kwargs = dict(
        id=uuid.uuid4(),
        mode="isolated",
        skill_name="billing",
        config={"seed": 3},
        secrets={},
        initial_skill=dict(SKILL),
        workspace_baseline=None,
        detector={},
        num_epochs=1,
        batch_size=2,
        steps_per_epoch=2,
        total_steps=2,
    )
    kwargs.update(overrides)
    return RunSpec(**kwargs)


def make_items(n, split="train"):
    return [
        Item(item_key=f"set:{split}_{i}", question=f"q{i}",
             ground_truth_response="gt", ground_truth_reasoning="r", ordinal=i)
        for i in range(n)
    ]


def make_rows(n, *, correct: int, activated=True):
    rows = []
    for i in range(n):
        row = ResultRow(item_key=f"k{i}", correlation_id=f"c{i}", status="done")
        row.verdict = "correct" if i < correct else "incorrect"
        row.judge_score = 1.0 if i < correct else 0.0
        row.agent_latency_ms = 100 + i
        row.activated = activated
        rows.append(row)
    return rows


def failed_rows(n, *, kind="agent"):
    """A rollout where nothing came back — the agent server is down."""
    return [
        ResultRow(item_key=f"k{i}", correlation_id=f"c{i}", status="failed",
                  failure_kind=kind)
        for i in range(n)
    ]


class Scores:
    """Scripts what each rollout returns, keyed by `(step_no, split)`.

    Every test here is really a statement about which skill was measured when,
    so the script is the test's premise and the assertions are about what the
    loop did with it. A script entry of `"fail"` is the other premise available:
    that split's questions never came back at all.
    """

    def __init__(self, script: dict, *, default=(4, 2)):
        self.script = script
        self.default = default
        self.calls: list[dict] = []

    def install(self, monkeypatch, store):
        async def fake_rollout(items, *, skill_files, mode, skill_name, seams,
                               config, **kwargs):
            step_no = len(store.steps) - 1 if store.steps else 0
            step_no = store.steps[-1]["step_no"] if store.steps else 0
            split = kwargs.get("_split") or self._split_of(items)
            self.calls.append({
                "step_no": step_no, "split": split,
                "skill_files": dict(skill_files), "n_items": len(items),
            })
            scripted = self.script.get((step_no, split), self.default)
            if scripted == "fail":
                return failed_rows(len(items))
            n, correct = scripted
            return make_rows(n, correct=correct)

        monkeypatch.setattr(engine, "run_rollout", fake_rollout)

    @staticmethod
    def _split_of(items):
        return "val" if items and "val_" in items[0].item_key else "train"

    def skill_used(self, step_no, split):
        return next(c["skill_files"] for c in self.calls
                    if c["step_no"] == step_no and c["split"] == split)


def install_update(monkeypatch, *, edits_line="2. Mention the period.", applied=1):
    """A stubbed update stage that appends one deterministic line."""

    def fake_update(*, files, skill_dir, mode, items, client, edit_budget, **kwargs):
        candidate = dict(files)
        # `skill_dir` is one directory or several — a routing run optimising
        # competing descriptions passes the whole set. The first is the run's
        # primary target, which is what these tests assert against.
        first = skill_dir if isinstance(skill_dir, str) else list(skill_dir)[0]
        entry = f"{first}/SKILL.md"
        candidate[entry] = candidate.get(entry, "") + edits_line + "\n"
        return UpdateOutcome(
            files=candidate,
            patch={"reasoning": "stubbed", "edits": []},
            reports=[],
            minibatches=[MinibatchRecord(
                minibatch_no=0, source_type="failure", n_items=len(items),
                item_keys=[i["id"] for i in items], prompt_system="s", prompt_user="u",
                raw_output={}, truncation=[], chars_before=10, chars_after=10,
                error=None, duration_ms=1,
            )],
            n_edits_merged=1, n_edits_ranked=1, n_edits_applied=applied,
            n_edits_skipped=0, edit_summary="stubbed", tokens={"calls": 1},
        )

    monkeypatch.setattr(engine, "run_update_stage", fake_update)
    return fake_update


def install_noop_update(monkeypatch):
    """An update stage where every edit was skipped — the candidate is the parent."""

    def fake_update(*, files, **kwargs):
        return UpdateOutcome(
            files=dict(files), patch={"reasoning": "nothing landed", "edits": []},
            reports=[], minibatches=[], n_edits_merged=0, n_edits_ranked=0,
            n_edits_applied=0, n_edits_skipped=2, edit_summary="nothing landed",
            tokens={"calls": 1},
        )

    monkeypatch.setattr(engine, "run_update_stage", fake_update)


def install_preflight(monkeypatch, *, activated=True, verified=True):
    """A pre-flight that clears, unless a test asks for one that does not.

    `verified` defaults to True because that is what a working agent produces
    and what every test here is otherwise about: the pre-flight now stops any
    run that has not *seen* the agent read the copy it was sent, so a helper
    that left it unset would fail every run in this file for a reason none of
    them are testing.
    """

    async def fake_probe(*args, **kwargs):
        row = ResultRow(item_key="probe", correlation_id="p", status="done")
        row.activated = activated
        row.detector_hit = "tool" if activated else "none"
        row.skills_read = ["billing"] if activated else []
        row.override_verified = verified
        return [row]

    monkeypatch.setattr(engine, "probe_activation", fake_probe)


class Seams:
    """The engine only passes seams through; nothing here is called."""

    agent = judge = trace = diagnosis = None
    optimizer = object()


async def run(store, monkeypatch, *, cancel_event=None, publish=None):
    events = [] if publish is None else publish

    async def collect(event):
        events.append(event)

    status = await engine.run_optimization(
        store.spec.id, store=store, seams=Seams(),
        publish=collect, cancel_event=cancel_event,
    )
    return status, events


# --- The baseline -----------------------------------------------------------


@pytest.mark.asyncio
async def test_step_zero_measures_validation_only(monkeypatch):
    """Step 0 is the initial skill on held-out data, and nothing else.

    Without it the chart cannot answer the only question that matters — did this
    run help? — because every later point would be relative to a number nobody
    recorded. And it must not roll out the *training* split: there is no
    candidate yet, so a train rollout at step 0 would be a batch of agent calls
    bought for a point the chart does not plot.
    """
    store = RecordingStore(make_spec(total_steps=0, steps_per_epoch=1),
                           make_items(4), make_items(4, "val"))
    Scores({(0, "val"): (4, 3)}).install(monkeypatch, store)
    install_preflight(monkeypatch)
    install_update(monkeypatch)

    await run(store, monkeypatch)

    assert store.splits_of(0) == ["val"]
    assert store.rollout(0, "val").hard == pytest.approx(0.75)
    assert store.step(0)["parent_step_no"] is None


@pytest.mark.asyncio
async def test_the_initial_skill_is_stored_so_every_diff_has_a_base(monkeypatch):
    """Part 2 offers 'vs initial'. That needs the initial bytes on disk.

    Reading them back off the agent server later would be reading a workspace
    that has since moved on.
    """
    store = RecordingStore(make_spec(total_steps=0, steps_per_epoch=1),
                           make_items(2), make_items(2, "val"))
    Scores({}).install(monkeypatch, store)
    install_preflight(monkeypatch)
    install_update(monkeypatch)

    await run(store, monkeypatch)

    initial = next(s for s in store.skills if s["kind"] == "initial")
    assert initial["files"] == SKILL
    assert initial["step_no"] == 0


# --- Accept, reject, and what the next step starts from ---------------------


@pytest.mark.asyncio
async def test_an_accepted_candidate_becomes_the_skill_the_next_step_measures(monkeypatch):
    """Accepting has to actually change what goes to the agent.

    If the loop kept scoring candidates but never adopted one, every step would
    be a fresh edit of the *initial* skill: the run would look busy, the chart
    would wander, and nothing would ever compound.
    """
    store = RecordingStore(make_spec(total_steps=2, steps_per_epoch=2),
                           make_items(4), make_items(4, "val"))
    scores = Scores({
        (0, "val"): (4, 2),      # baseline 0.50
        (1, "train"): (2, 1),
        (1, "val"): (4, 3),      # candidate 0.75 -> accepted
        (2, "train"): (2, 1),
        (2, "val"): (4, 3),
    })
    scores.install(monkeypatch, store)
    install_preflight(monkeypatch)
    install_update(monkeypatch)

    await run(store, monkeypatch)

    assert store.step(1)["gate_action"] == "accept_new_best"
    # Step 2's training rollout runs against step 1's accepted candidate.
    assert "Mention the period." in scores.skill_used(2, "train")["billing/SKILL.md"]


@pytest.mark.asyncio
async def test_a_rejected_candidate_is_rolled_back_before_the_next_step(monkeypatch):
    """Reject means the edits are gone, not merely unrecorded.

    This is the failure that would quietly ruin a run: if a rejected candidate
    stayed in `current`, every later step would build on edits the validation
    split had already refused, and the gate's comparisons would be against a
    skill that was never accepted.
    """
    store = RecordingStore(make_spec(total_steps=2, steps_per_epoch=2),
                           make_items(4), make_items(4, "val"))
    scores = Scores({
        (0, "val"): (4, 3),      # baseline 0.75
        (1, "train"): (2, 1),
        (1, "val"): (4, 1),      # candidate 0.25 -> rejected
        (2, "train"): (2, 1),
        (2, "val"): (4, 4),
    })
    scores.install(monkeypatch, store)
    install_preflight(monkeypatch)
    install_update(monkeypatch)

    await run(store, monkeypatch)

    assert store.step(1)["gate_action"] == "reject"
    assert scores.skill_used(2, "train") == SKILL


@pytest.mark.asyncio
async def test_parent_step_is_the_last_accepted_step_not_the_previous_one(monkeypatch):
    """The diff's baseline is the skill this step actually started from.

    After a rejection, `step_no - 1` names a candidate that was thrown away.
    Diffing against it would show the developer a change that never existed —
    the inverse of the rejected edits, presented as this step's work.
    """
    store = RecordingStore(make_spec(total_steps=3, steps_per_epoch=3),
                           make_items(6), make_items(4, "val"))
    Scores({
        (0, "val"): (4, 2),      # 0.50
        (1, "val"): (4, 3),      # 0.75 accepted
        (2, "val"): (4, 1),      # rejected
        (3, "val"): (4, 4),
    }).install(monkeypatch, store)
    install_preflight(monkeypatch)
    install_update(monkeypatch)

    await run(store, monkeypatch)

    assert store.step(2)["parent_step_no"] == 1
    assert store.step(3)["parent_step_no"] == 1


@pytest.mark.asyncio
async def test_the_training_rollout_uses_the_current_skill_and_validation_the_candidate(
    monkeypatch,
):
    """The two points on the chart are measurements of different skills.

    Train is measured *before* the edit and validation *after* it — that is what
    the half-step offset on the x-axis means. Rolling out training against the
    candidate would make the two lines describe the same skill, and the gap
    between them would stop meaning anything.
    """
    store = RecordingStore(make_spec(total_steps=1, steps_per_epoch=1),
                           make_items(2), make_items(2, "val"))
    scores = Scores({(0, "val"): (2, 1), (1, "train"): (2, 1), (1, "val"): (2, 2)})
    scores.install(monkeypatch, store)
    install_preflight(monkeypatch)
    install_update(monkeypatch)

    await run(store, monkeypatch)

    assert scores.skill_used(1, "train") == SKILL
    assert "Mention the period." in scores.skill_used(1, "val")["billing/SKILL.md"]


# --- The candidate cache ----------------------------------------------------


@pytest.mark.asyncio
async def test_a_candidate_identical_to_the_current_skill_skips_its_validation_rollout(
    monkeypatch,
):
    """A whole split of agent calls, bought to learn something already known.

    When every proposed edit is skipped — a wrong target string, a protected
    region, a bad path — the candidate is byte-identical to the skill just
    measured. Re-running validation on it costs the same as a real step and
    cannot return anything new.
    """
    store = RecordingStore(make_spec(total_steps=1, steps_per_epoch=1),
                           make_items(2), make_items(2, "val"))
    scores = Scores({(0, "val"): (2, 1), (1, "train"): (2, 1)})
    scores.install(monkeypatch, store)
    install_preflight(monkeypatch)
    install_noop_update(monkeypatch)

    await run(store, monkeypatch)

    assert "val" not in store.splits_of(1)
    assert store.step(1)["candidate_from_cache"] is True


@pytest.mark.asyncio
async def test_a_cached_candidate_is_still_gated_and_still_rejected_on_a_tie(monkeypatch):
    """Reusing the score must not also skip the decision.

    A candidate equal to the current skill scores equal to it, and equal is not
    better. If the cache path returned early without gating, that candidate
    would be adopted for free and the loop would accept every no-op step.
    """
    store = RecordingStore(make_spec(total_steps=1, steps_per_epoch=1),
                           make_items(2), make_items(2, "val"))
    Scores({(0, "val"): (2, 1), (1, "train"): (2, 1)}).install(monkeypatch, store)
    install_preflight(monkeypatch)
    install_noop_update(monkeypatch)

    await run(store, monkeypatch)

    assert store.step(1)["gate_action"] == "reject"


# --- Cancellation, resumption, and always closing the stream ----------------


@pytest.mark.asyncio
async def test_cancelling_keeps_the_steps_that_finished(monkeypatch):
    """Stopping a run must not throw away what it already bought.

    Every completed step is a paid-for measurement and a downloadable skill. A
    cancel that discarded them would make the stop button the most expensive
    control in the product, and developers would stop using it on runs that had
    plainly gone wrong.
    """
    store = RecordingStore(make_spec(total_steps=3, steps_per_epoch=3),
                           make_items(6), make_items(2, "val"))
    Scores({(0, "val"): (2, 1), (1, "val"): (2, 2)}).install(monkeypatch, store)
    install_preflight(monkeypatch)
    install_update(monkeypatch)

    cancel = asyncio.Event()

    original = engine.run_update_stage

    def cancel_after_first_step(**kwargs):
        cancel.set()
        return original(**kwargs)

    monkeypatch.setattr(engine, "run_update_stage", cancel_after_first_step)

    status, events = await run(store, monkeypatch, cancel_event=cancel)

    assert status == "cancelled"
    # The baseline finished before the stop, and stays finished.
    assert store.step(0)["status"] == "done"
    assert store.rollout(0, "val").hard == pytest.approx(0.5)
    # Step 1 was interrupted between its two rollouts, so it is neither 'done'
    # (there is no candidate score) nor left 'running' (the UI would show a live
    # step on a finished run). Its training rollout was still paid for and is
    # still on record.
    assert store.step(1)["status"] == "aborted"
    assert store.splits_of(1) == ["train"]
    assert not any(s["step_no"] == 3 for s in store.steps)
    assert events[-1]["type"] == "run_completed"


@pytest.mark.asyncio
async def test_a_durable_cancel_request_is_honoured_even_without_the_event(monkeypatch):
    """The in-process event dies with the process; the database flag does not.

    A run cancelled while the backend was restarting has only the flag. If the
    resumed run consulted the event alone it would carry on spending money on a
    run the developer stopped ten minutes ago.
    """
    store = RecordingStore(make_spec(total_steps=2, steps_per_epoch=2),
                           make_items(4), make_items(2, "val"))
    Scores({(0, "val"): (2, 1)}).install(monkeypatch, store)
    install_preflight(monkeypatch)
    install_update(monkeypatch)
    store.cancel = True

    status, _ = await run(store, monkeypatch)

    assert status == "cancelled"


@pytest.mark.asyncio
async def test_resuming_does_not_re_run_the_steps_that_already_finished(monkeypatch):
    """Checkpointing per step is the whole reason a run survives a restart.

    Re-running from step 0 would pay for every completed step a second time and
    overwrite measurements the chart is already showing — and because rollouts
    are sampled, the replacements would not even be the same numbers.
    """
    resume = engine.ResumeState(
        last_step_no=1,
        current_files={"billing/SKILL.md": "# Billing\n\nedited\n"},
        current_score=0.75, best_files={"billing/SKILL.md": "# Billing\n\nedited\n"},
        best_score=0.75, best_step=1, parent_step_no=1, score_cache={},
    )
    store = RecordingStore(make_spec(total_steps=2, steps_per_epoch=2),
                           make_items(4), make_items(2, "val"), resume=resume)
    scores = Scores({(2, "train"): (2, 1), (2, "val"): (2, 2)})
    scores.install(monkeypatch, store)
    install_preflight(monkeypatch)
    install_update(monkeypatch)

    await run(store, monkeypatch)

    assert [s["step_no"] for s in store.steps] == [2]
    assert scores.skill_used(2, "train") == resume.current_files


@pytest.mark.asyncio
async def test_a_resumed_run_does_not_repeat_the_pre_flight_probe(monkeypatch):
    """The probe answers a question about the workspace, once, at the start.

    Asking again on every restart would bill for an agent call per restart and,
    worse, could abort a half-finished routing run over a transient trace
    failure — discarding steps that were already paid for.
    """
    resume = engine.ResumeState(
        last_step_no=1, current_files=dict(SKILL), current_score=0.5,
        best_files=dict(SKILL), best_score=0.5, best_step=1,
        parent_step_no=1, score_cache={},
    )
    store = RecordingStore(make_spec(total_steps=2, steps_per_epoch=2),
                           make_items(4), make_items(2, "val"), resume=resume)
    Scores({}).install(monkeypatch, store)
    install_update(monkeypatch)

    probes = []

    async def counting_probe(*args, **kwargs):
        probes.append(1)
        return []

    monkeypatch.setattr(engine, "probe_activation", counting_probe)

    await run(store, monkeypatch)

    assert probes == []


@pytest.mark.asyncio
async def test_the_terminal_event_goes_out_even_when_the_loop_raises(monkeypatch):
    """A stream nobody closes is a UI that waits forever.

    The orchestrator learned this for eval runs; an optimization run is longer,
    so the same bug is more expensive here. Whatever happens, the last thing out
    is `run_completed` and the run row is terminal.
    """
    store = RecordingStore(make_spec(total_steps=1, steps_per_epoch=1),
                           make_items(2), make_items(2, "val"))
    install_preflight(monkeypatch)

    async def exploding_rollout(*args, **kwargs):
        raise RuntimeError("the agent server fell over")

    monkeypatch.setattr(engine, "run_rollout", exploding_rollout)

    status, events = await run(store, monkeypatch)

    assert status == "failed"
    assert events[-1]["type"] == "run_completed"
    assert "fell over" in store.final["error_message"]


# --- Refusing to score a batch that mostly failed ---------------------------
#
# A question that never came back is not the skill being wrong, and a split
# measured on the questions that happened to answer is not a smaller
# measurement but an unrepresentative one. These tests are about what that
# costs. It used to cost the whole run: the split was bought a second time and
# a second failure raised, so an outage in the last five minutes of an hour
# threw away every finished step. It now costs the step it happened in, and
# `stopping.py` decides when a run of them has become an outage worth stopping
# for.


@pytest.mark.asyncio
async def test_a_training_batch_that_never_came_back_costs_its_step_not_the_run(monkeypatch):
    """No trajectories, no candidate, and — the expensive half — no validation.

    Reflecting on whichever questions the outage spared would argue a skill edit
    from a network problem, and then a whole validation split would be spent
    measuring it.
    """
    store = RecordingStore(make_spec(total_steps=2, steps_per_epoch=2),
                           make_items(4), make_items(4, "val"))
    scores = Scores({(1, "train"): "fail"})
    scores.install(monkeypatch, store)
    install_preflight(monkeypatch)
    update = install_update(monkeypatch)
    called = []
    monkeypatch.setattr(
        engine, "run_update_stage",
        lambda **kwargs: called.append(kwargs) or update(**kwargs),
    )

    status, _ = await run(store, monkeypatch)

    assert status == "completed"
    assert store.step(1)["gate_action"] == "skip"
    assert store.step(1)["gate_reject_reason"] == "train_errors"
    assert store.splits_of(1) == ["train"]
    # One update stage for the whole run: step 2's. Step 1 never called it.
    assert len(called) == 1


@pytest.mark.asyncio
async def test_a_skipped_step_leaves_the_skill_and_the_scores_alone(monkeypatch):
    """It produced nothing, so it must change nothing.

    Including the two score columns, which a resumed run replays to rebuild its
    working state — a null there would read as the run having lost its score.
    """
    store = RecordingStore(make_spec(total_steps=2, steps_per_epoch=2),
                           make_items(4), make_items(4, "val"))
    scores = Scores({(1, "train"): "fail"})
    scores.install(monkeypatch, store)
    install_preflight(monkeypatch)
    install_update(monkeypatch)

    await run(store, monkeypatch)

    assert store.step(1)["current_score"] == store.step(0)["current_score"]
    assert store.step(1)["best_score"] == store.step(0)["best_score"]
    # Step 2 trains on the skill the baseline measured, not on some half-edited
    # candidate the skipped step left behind.
    assert scores.skill_used(2, "train") == SKILL


@pytest.mark.asyncio
async def test_a_refused_validation_split_drops_the_candidate_without_gating_it(monkeypatch):
    """There is no number to gate on, so nothing may be accepted.

    Accepting here would mean accepting an edit on the strength of whichever
    questions the agent server happened to answer — which is how a rollout
    failure turns into a permanent change to the skill.
    """
    store = RecordingStore(make_spec(total_steps=2, steps_per_epoch=2),
                           make_items(4), make_items(4, "val"))
    scores = Scores({(1, "val"): "fail"})
    scores.install(monkeypatch, store)
    install_preflight(monkeypatch)
    install_update(monkeypatch)

    status, _ = await run(store, monkeypatch)

    assert status == "completed"
    assert store.step(1)["gate_action"] == "reject"
    assert store.step(1)["gate_reject_reason"] == "val_errors"
    # The edits are still recorded — the diff is how anyone finds out what was
    # thrown away — but the skill in force is the one the step started from.
    assert store.step(1)["lines_added"] == 1
    assert scores.skill_used(2, "train") == SKILL


@pytest.mark.asyncio
async def test_a_refused_validation_score_never_reaches_the_candidate_cache(monkeypatch):
    """The cache is keyed by the skill's hash and read for the rest of the run.

    A refused score in it would be handed to the gate on some later step as
    though it had been measured, with nothing on screen to say otherwise.
    """
    store = RecordingStore(make_spec(total_steps=2, steps_per_epoch=2),
                           make_items(4), make_items(4, "val"))
    scores = Scores({(1, "val"): "fail"})
    scores.install(monkeypatch, store)
    install_preflight(monkeypatch)
    # Both steps produce the identical candidate, so a cached score would show
    # up as step 2 skipping its validation rollout.
    install_update(monkeypatch)

    await run(store, monkeypatch)

    assert store.splits_of(1) == ["train", "val"]
    assert store.splits_of(2) == ["train", "val"]


@pytest.mark.asyncio
async def test_the_baseline_failing_still_ends_the_run(monkeypatch):
    """The one rollout whose failure is fatal, because nothing survives it.

    Every later number is a comparison against the baseline, so a run that
    started from an unmeasured one would report improvements over nothing.
    """
    store = RecordingStore(make_spec(total_steps=2, steps_per_epoch=2),
                           make_items(4), make_items(4, "val"))
    Scores({(0, "val"): "fail"}).install(monkeypatch, store)
    install_preflight(monkeypatch)
    install_update(monkeypatch)

    status, events = await run(store, monkeypatch)

    assert status == "failed"
    assert "baseline" in store.final["error_message"]
    assert events[-1]["type"] == "run_completed"


# --- Stopping early ---------------------------------------------------------


@pytest.mark.asyncio
async def test_refusals_in_a_row_stop_the_run_and_say_why(monkeypatch):
    store = RecordingStore(
        make_spec(total_steps=4, steps_per_epoch=4,
                  config={"seed": 3, "early_stop_val_error_streak": 2}),
        make_items(8), make_items(4, "val"),
    )
    Scores({(1, "val"): "fail", (2, "val"): "fail"}).install(monkeypatch, store)
    install_preflight(monkeypatch)
    install_update(monkeypatch)

    status, events = await run(store, monkeypatch)

    assert status == "completed"
    assert store.final["stop_reason"] == "early_stop_val_errors"
    # Steps 3 and 4 were never bought.
    assert [s["step_no"] for s in store.steps] == [0, 1, 2]
    assert events[-1]["stop_reason"] == "early_stop_val_errors"


@pytest.mark.asyncio
async def test_one_split_that_answers_clears_the_streak(monkeypatch):
    """Consecutive, not cumulative.

    Three bad rollouts spread over a run is a flaky afternoon; only three in a
    row are an agent server that has stopped answering.
    """
    store = RecordingStore(
        make_spec(total_steps=3, steps_per_epoch=3,
                  config={"seed": 3, "early_stop_val_error_streak": 2}),
        make_items(6), make_items(4, "val"),
    )
    Scores({(1, "val"): "fail", (3, "val"): "fail"}).install(monkeypatch, store)
    install_preflight(monkeypatch)
    install_update(monkeypatch)

    status, _ = await run(store, monkeypatch)

    assert status == "completed"
    assert store.final["stop_reason"] == "finished"
    assert [s["step_no"] for s in store.steps] == [0, 1, 2, 3]


@pytest.mark.asyncio
async def test_a_run_that_has_stopped_improving_runs_out_of_patience(monkeypatch):
    store = RecordingStore(
        make_spec(total_steps=5, steps_per_epoch=5,
                  config={"seed": 3, "early_stop_patience": 2}),
        make_items(10), make_items(4, "val"),
    )
    # The baseline scores 50% and nothing beats it.
    Scores({}, default=(4, 2)).install(monkeypatch, store)
    install_preflight(monkeypatch)
    install_update(monkeypatch)

    status, _ = await run(store, monkeypatch)

    assert store.final["stop_reason"] == "early_stop_patience"
    assert [s["step_no"] for s in store.steps] == [0, 1, 2]
    assert status == "completed"


@pytest.mark.asyncio
async def test_reaching_the_target_ends_the_run_as_a_success(monkeypatch):
    store = RecordingStore(
        make_spec(total_steps=4, steps_per_epoch=4,
                  config={"seed": 3, "early_stop_target_score": 0.75}),
        make_items(8), make_items(4, "val"),
    )
    Scores({(0, "val"): (4, 2), (1, "val"): (4, 3)}).install(monkeypatch, store)
    install_preflight(monkeypatch)
    install_update(monkeypatch)

    status, _ = await run(store, monkeypatch)

    assert status == "completed"
    assert store.final["stop_reason"] == "early_stop_target"
    assert [s["step_no"] for s in store.steps] == [0, 1]


@pytest.mark.asyncio
async def test_a_step_that_measured_nothing_cannot_reach_the_target(monkeypatch):
    """A refused split has no score, and None is not a high one.

    Otherwise an outage would end a run by declaring it a success.
    """
    store = RecordingStore(
        make_spec(total_steps=1, steps_per_epoch=1,
                  config={"seed": 3, "early_stop_target_score": 0.0}),
        make_items(4), make_items(4, "val"),
    )
    Scores({(0, "val"): (4, 0), (1, "val"): "fail"}).install(monkeypatch, store)
    install_preflight(monkeypatch)
    install_update(monkeypatch)

    await run(store, monkeypatch)

    assert store.final["stop_reason"] == "finished"


@pytest.mark.asyncio
async def test_a_resumed_run_keeps_counting_refusals_from_before_the_restart(monkeypatch):
    """A counter that resets on every restart is one a crash loop never trips.

    The streak is rebuilt from the step rows (`store.load_resume_state`), so a
    run whose agent server went down before the backend did stops on the step
    the policy says rather than three steps later.
    """
    resume = ResumeState(
        last_step_no=2, current_files=dict(SKILL), current_score=0.5,
        best_files=dict(SKILL), best_score=0.5, best_step=0, parent_step_no=None,
        val_error_streak=1,
    )
    store = RecordingStore(
        make_spec(total_steps=6, steps_per_epoch=6,
                  config={"seed": 3, "early_stop_val_error_streak": 2}),
        make_items(12), make_items(4, "val"), resume=resume,
    )
    Scores({(3, "val"): "fail"}).install(monkeypatch, store)
    install_preflight(monkeypatch)
    install_update(monkeypatch)

    await run(store, monkeypatch)

    assert store.final["stop_reason"] == "early_stop_val_errors"
    assert [s["step_no"] for s in store.steps] == [3]


# --- Pre-flight -------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["isolated", "routing"])
async def test_a_run_refuses_to_start_when_the_agent_cannot_be_seen(monkeypatch, mode):
    """One rule for both modes, and for isolated that is a change.

    Isolated used to carry on here. Its gate was judge accuracy, which is
    measurable whether or not the agent announces what it read, so a silent
    detector cost it one column in the UI and nothing else — and refusing would
    have locked out every agent whose traces did not name skill file paths.

    What that reasoning left out is that the same silence also hides whether the
    candidate reached the agent at all. Such a run can spend an hour measuring
    the skill already deployed on the agent, and report the resulting flat line
    as a finding. The column was worth losing; the measurement is not.

    So the pre-flight now asks one question in both modes — did we *see* the
    agent read the copy we sent — and a run that cannot answer it stops before
    it buys anything. `tests/test_optimizer_preflight_override.py` holds the
    detail of what counts as an answer.
    """
    store = RecordingStore(make_spec(mode=mode), make_items(4), make_items(4, "val"))
    Scores({}).install(monkeypatch, store)
    install_preflight(monkeypatch, activated=False, verified=None)
    install_update(monkeypatch)

    status, events = await run(store, monkeypatch)

    assert status == "failed"
    assert not store.steps, "nothing was bought"
    assert next(e for e in events if e["type"] == "preflight")["ok"] is False


def _detector_of(store):
    """The detector settings as the run last persisted them."""
    return next(u["detector"] for u in reversed(store.run_updates) if "detector" in u)


@pytest.mark.asyncio
async def test_the_probe_records_what_it_found_for_a_resumed_run(monkeypatch):
    """The probe is bought once, so what it found has to outlive this process.

    It runs on a fresh start only — re-probing on every restart would bill an
    agent call per restart and could abort a half-finished routing run over one
    transient trace failure — so a run resumed after a backend restart reads the
    verdict back rather than establishing it again.

    There used to be a `detectable` flag here as well, and it was the thing that
    decided whether a later "nothing was seen" meant *no* or *unknown*. It is
    gone: the detector now answers `False` for a trajectory that landed carrying
    no body text and `None` only when nothing landed at all, which is sound
    because a run whose agent cannot be seen into does not get past the
    pre-flight at all.
    """
    store = RecordingStore(make_spec(total_steps=1, steps_per_epoch=1),
                           make_items(2), make_items(2, "val"))
    Scores({(0, "val"): (2, 1), (1, "train"): (2, 1), (1, "val"): (2, 2)}).install(
        monkeypatch, store
    )
    install_preflight(monkeypatch, activated=True)
    install_update(monkeypatch)

    await run(store, monkeypatch)

    preflight = _detector_of(store)["preflight"]
    assert preflight["ok"] is True
    assert "detectable" not in _detector_of(store)


@pytest.mark.asyncio
async def test_a_detector_key_from_an_older_run_is_carried_not_rejected(monkeypatch):
    """A run created before this shape existed must still resume.

    `detector` is a JSONB column holding whatever the run was created with, and
    rows in the wild carry `detectable` and `path_patterns` — settings nothing
    reads any more. Stripping them would be a migration; failing on them would
    strand every in-flight run at the moment this deploys.
    """
    spec = make_spec(total_steps=1, steps_per_epoch=1)
    spec.detector.update({"detectable": True, "path_patterns": [r"skills/([a-z]+)/"]})
    store = RecordingStore(spec, make_items(2), make_items(2, "val"))
    Scores({(0, "val"): (2, 1), (1, "train"): (2, 1), (1, "val"): (2, 2)}).install(
        monkeypatch, store
    )
    install_preflight(monkeypatch, activated=True)
    install_update(monkeypatch)

    status, _ = await run(store, monkeypatch)

    assert status == "completed"
    assert _detector_of(store)["path_patterns"] == [r"skills/([a-z]+)/"]


@pytest.mark.asyncio
async def test_a_silent_probe_leaves_absence_meaning_nothing(monkeypatch):
    """The other direction, and the reason the flag is not simply always on.

    When nothing proved a detector fires here, "no skill read" is genuinely
    unobservable rather than false — reporting 0% activation for an agent whose
    skill loading we cannot see would condemn a run that is working fine. So an
    isolated run carries on with the column reading 'unknown', and `detectable`
    stays off.
    """
    store = RecordingStore(make_spec(mode="isolated", total_steps=1, steps_per_epoch=1),
                           make_items(2), make_items(2, "val"))
    Scores({(0, "val"): (2, 1), (1, "train"): (2, 1), (1, "val"): (2, 2)}).install(
        monkeypatch, store
    )
    install_preflight(monkeypatch, activated=False)
    install_update(monkeypatch)

    await run(store, monkeypatch)

    assert _detector_of(store).get("detectable") is not True
    assert store.spec.detector.get("detectable") is not True


# --- Batching ---------------------------------------------------------------


def test_consecutive_steps_in_an_epoch_train_on_different_questions():
    """A step is one minibatch, and an epoch is supposed to cover the split.

    If every step drew the same slice, the extra steps would re-fit the same
    handful of questions and the rest of the training set would never be seen —
    an epoch in name only.
    """
    items = make_items(6)
    first = engine.train_batch(items, epoch_no=1, step_in_epoch=1, batch_size=2, seed=1)
    second = engine.train_batch(items, epoch_no=1, step_in_epoch=2, batch_size=2, seed=1)
    third = engine.train_batch(items, epoch_no=1, step_in_epoch=3, batch_size=2, seed=1)
    keys = [i.item_key for batch in (first, second, third) for i in batch]
    assert sorted(keys) == sorted(i.item_key for i in items)


def test_the_same_seed_and_epoch_reproduce_the_same_batch():
    """A resumed run must re-derive the batch it was going to use.

    The batch composition is not stored anywhere; it is recomputed. If the
    shuffle were unseeded, a restart mid-epoch would silently retrain on a
    different sample than the one the interrupted run had planned.
    """
    items = make_items(6)
    once = engine.train_batch(items, epoch_no=2, step_in_epoch=1, batch_size=3, seed=42)
    twice = engine.train_batch(items, epoch_no=2, step_in_epoch=1, batch_size=3, seed=42)
    assert [i.item_key for i in once] == [i.item_key for i in twice]


# --- The whole thing, on the fake seams -------------------------------------


@pytest.mark.asyncio
async def test_a_complete_run_executes_against_the_fake_layer(monkeypatch, configure):
    """Nothing stubbed but the store: real rollouts, real reflect, real apply.

    Every part above is tested in isolation, which is exactly why this exists —
    a loop whose pieces all pass individually can still be wired up wrong, and
    the failure modes that only appear here are the ones that involve two
    correct components disagreeing about a contract. It is also the property the
    README claims and this feature had to preserve: the whole product is
    demonstrable on Docker alone, with no LLM endpoint and no agent server.
    """
    from app import fake_config
    from app.integrations import build_seams
    from app.integrations.fake import _FAKE_SKILL_FILES

    # The fake seams simulate 1-3s per agent call, which is the right feel for a
    # demo and the wrong one for a test that makes thirty of them. Only the
    # sleeping is removed; every call, poll and retry still happens.
    for name in ("AGENT_LATENCY_MIN_S", "AGENT_LATENCY_MAX_S", "JUDGE_LATENCY_MIN_S",
                 "JUDGE_LATENCY_MAX_S", "TRACE_FETCH_LATENCY_S"):
        monkeypatch.setattr(fake_config, name, 0.0)

    skill = {p: t for p, t in _FAKE_SKILL_FILES.items() if p.startswith("billing/")}
    spec = make_spec(
        initial_skill=skill,
        config={"seed": 1, "minibatch_size": 2, "concurrency": 4, "learning_rate": 3},
        total_steps=2, steps_per_epoch=2, batch_size=3,
    )
    train = [
        Item(item_key=f"set:train_{i}", question=f"What is the balance for account {i}?",
             ground_truth_response=f"Account {i} owes $100.", ground_truth_reasoning="r",
             ordinal=i)
        for i in range(6)
    ]
    val = [
        Item(item_key=f"set:val_{i}", question=f"What did account {i} pay in Q2?",
             ground_truth_response=f"Account {i} paid $50.", ground_truth_reasoning="r",
             ordinal=i)
        for i in range(4)
    ]
    store = RecordingStore(spec, train, val)
    events: list[dict] = []

    with configure(trace_poll_backoff_s=[0.0, 0.0, 0.0]):
        status = await engine.run_optimization(
            spec.id, store=store,
            seams=build_seams(spec.config, include_optimizer=True),
            publish=_collect(events),
        )

    assert status == "completed"
    assert [s["step_no"] for s in store.steps] == [0, 1, 2]
    # The baseline measured validation; each later step measured both splits or
    # reused a cached score for an unchanged candidate.
    assert store.splits_of(0) == ["val"]
    assert "train" in store.splits_of(1)
    # A real analyst call happened, with a real prompt, on real trajectories.
    assert store.minibatches
    prompt = store.minibatches[0]["prompt_user"]
    assert "### Trajectory 1" in prompt
    assert store.minibatches[0]["raw_output"]["failure_summary"]
    # The four things a reviewer of a failure needs, in the prompt itself.
    for heading in ("#### Task", "#### Agent Response", "#### Ground-truth Response",
                    "Failure Reason (from the judge)"):
        assert heading in prompt, heading
    # And the tool catalogue exactly once for the whole batch — not once per
    # step, which is what used to overflow the context window, and not once per
    # trajectory either: they were all answered by the same agent under the same
    # skill, so it is hoisted. A regression shows up here as a count that scales
    # with either the steps or the trajectories.
    assert prompt.count("#### Tools Available") == 1
    assert prompt.count("#### System Prompt") == 1
    assert prompt.count("### Trajectory ") > 1
    # What became of those patches is recorded too: with one analyst there is
    # nothing to merge hierarchically, but the two groups are still combined.
    assert [c["stage"] for c in store.stage_calls]
    assert all(c["prompt_user"] for c in store.stage_calls)
    # And a real candidate skill was produced and stored.
    candidates = [s for s in store.skills if s["kind"] == "candidate"]
    assert candidates
    assert set(candidates[0]["files"]) >= set(skill)
    assert events[-1]["type"] == "run_completed"


def _collect(sink):
    async def publish(event):
        sink.append(event)

    return publish


def test_a_later_epoch_reshuffles():
    """Same order every epoch means the minibatches never change composition.

    Reflection is over a *group* of trajectories; fixing the groups for the whole
    run means the same questions are always compared with the same neighbours,
    and patterns that only show up across groups never surface.
    """
    items = make_items(8)
    first = engine.train_batch(items, epoch_no=1, step_in_epoch=1, batch_size=4, seed=5)
    later = engine.train_batch(items, epoch_no=2, step_in_epoch=1, batch_size=4, seed=5)
    assert [i.item_key for i in first] != [i.item_key for i in later]


# --- What the step row keeps of the update stage -----------------------------


@pytest.mark.asyncio
async def test_the_per_edit_apply_report_reaches_the_step_row(monkeypatch):
    """A count cannot say *why* an edit never landed, and nothing can recover it.

    `apply_patch_with_report` decides each edit's status while it applies the
    patch — "the target string was not in the file", "that path is outside the
    skill", "that region is protected". None of it is derivable afterwards from
    the before and after snapshots, so if the engine forwards only
    `n_edits_skipped` the reason is gone for good and Part 2 can only say "2
    edits were skipped", which is compatible with three different problems that
    call for three different responses.
    """
    reports = [
        {"index": 1, "op": "append", "path": "billing/SKILL.md", "path_defaulted": False,
         "target": "", "content_preview": "Mention the period.",
         "status": "applied_append"},
        {"index": 2, "op": "replace", "path": "billing/SKILL.md", "path_defaulted": False,
         "target": "a line that is not there", "content_preview": "…",
         "status": "skipped_replace_target_not_found"},
    ]

    def fake_update(*, files, skill_dir, **kwargs):
        candidate = dict(files)
        entry = f"{skill_dir}/SKILL.md"
        candidate[entry] = candidate.get(entry, "") + "Mention the period.\n"
        return UpdateOutcome(
            files=candidate, patch={"reasoning": "stubbed", "edits": []},
            reports=reports, minibatches=[], n_edits_merged=2, n_edits_ranked=2,
            n_edits_applied=1, n_edits_skipped=1, edit_summary="one of two landed",
            tokens={"calls": 1},
        )

    store = RecordingStore(make_spec(total_steps=1, steps_per_epoch=1),
                           make_items(2), make_items(2, "val"))
    Scores({}).install(monkeypatch, store)
    install_preflight(monkeypatch)
    monkeypatch.setattr(engine, "run_update_stage", fake_update)

    await run(store, monkeypatch)

    assert store.step(1)["edit_reports"] == reports
    assert store.step(1)["n_edits_skipped"] == 1


@pytest.mark.asyncio
async def test_a_candidate_that_memorised_a_gold_answer_is_counted_at_write_time(monkeypatch):
    """The overview warns about leaks, and it cannot afford to look for them.

    Finding a leak means diffing a step's candidate against its parent and
    searching the added lines — quadratic in the file, once per step. The run
    overview streams and reloads on every terminal event, so doing that at read
    time would put a whole run's worth of diffs inside a request that fires
    repeatedly while the run is live. It is measured once, here, exactly as
    `lines_added` is.

    The count is taken against the *parent* skill and the *training* answers,
    which is what `GET .../steps/{n}/skill?base=parent` reports. If the two ever
    drift, the overview and Part 2 disagree about the same step.
    """
    leaked = "The Northwind Q2 balance is $42,180.00."

    def fake_update(*, files, skill_dir, **kwargs):
        candidate = dict(files)
        entry = f"{skill_dir}/SKILL.md"
        candidate[entry] = candidate.get(entry, "") + f"If asked: {leaked}\n"
        return UpdateOutcome(
            files=candidate, patch={"reasoning": "stubbed", "edits": []}, reports=[],
            minibatches=[], n_edits_merged=1, n_edits_ranked=1, n_edits_applied=1,
            n_edits_skipped=0, edit_summary="stubbed", tokens={},
        )

    train = [
        Item(item_key="set:t0", question="q0", ground_truth_response=leaked,
             ground_truth_reasoning="r", ordinal=0),
        Item(item_key="set:t1", question="q1", ground_truth_response="something else",
             ground_truth_reasoning="r", ordinal=1),
    ]
    store = RecordingStore(make_spec(total_steps=1, steps_per_epoch=1),
                           train, make_items(2, "val"))
    Scores({}).install(monkeypatch, store)
    install_preflight(monkeypatch)
    monkeypatch.setattr(engine, "run_update_stage", fake_update)

    await run(store, monkeypatch)

    assert store.step(1)["n_answer_leaks"] == 1


@pytest.mark.asyncio
async def test_an_ordinary_edit_records_no_leak(monkeypatch):
    """The number only means something if it is usually zero.

    A check that fired on every step would make the warning noise, and the run
    that actually memorised an answer would be the one nobody looked at.
    """
    store = RecordingStore(make_spec(total_steps=1, steps_per_epoch=1),
                           make_items(2), make_items(2, "val"))
    Scores({}).install(monkeypatch, store)
    install_preflight(monkeypatch)
    install_update(monkeypatch, edits_line="Quote the period as well.")

    await run(store, monkeypatch)

    assert store.step(1)["n_answer_leaks"] == 0


# --- The agent that changed underneath the run -------------------------------


class _SeamsWith(Seams):
    """The shared stub plus the seams only a few tests need to be real."""

    def __init__(self, workspace=None, optimizer=None):
        self.workspace = workspace
        if optimizer is not None:
            self.optimizer = optimizer


class _Workspace:
    """A workspace seam that reports whatever version it is told to, in order."""

    def __init__(self, *versions):
        self.versions = list(versions)
        self.calls = 0

    async def get_version(self):
        self.calls += 1
        return self.versions[min(self.calls - 1, len(self.versions) - 1)]

    async def get_workspace(self):  # pragma: no cover - not used by the loop
        raise NotImplementedError


@pytest.mark.asyncio
async def test_each_step_records_the_agent_version_it_actually_ran_against(monkeypatch):
    """A run is a comparison, and a comparison needs the other side to hold still.

    Every number on the chart is "this skill, on that agent". If somebody deploys
    a config change to the agent server halfway through, the steps before and
    after it are measuring different systems — and the gate will accept or reject
    a candidate for a reason that has nothing to do with the edits. Nothing else
    in the run would ever show it: the accuracy simply moves.

    Recorded per step rather than as one run-level flag, because *which* steps
    are comparable is the question a reader actually has.
    """
    store = RecordingStore(make_spec(total_steps=2, steps_per_epoch=2, workspace_version="cfg-1"),
                           make_items(4), make_items(4, "val"))
    Scores({}).install(monkeypatch, store)
    install_preflight(monkeypatch)
    install_update(monkeypatch)

    await engine.run_optimization(
        store.spec.id, store=store,
        seams=_SeamsWith(_Workspace("cfg-1", "cfg-1", "cfg-2")),
        publish=_collect([]), cancel_event=None,
    )

    # The baseline is probed too: every later step is compared against it, so a
    # deploy before step 0 invalidates the whole chart rather than one point.
    assert store.step(0)["workspace_version"] == "cfg-1"
    assert store.step(1)["workspace_version"] == "cfg-1"
    assert store.step(2)["workspace_version"] == "cfg-2"


@pytest.mark.asyncio
async def test_a_version_probe_that_fails_does_not_end_the_run(monkeypatch):
    """The probe is an observation, not a dependency.

    An hour of agent calls is already paid for by the time this runs. Letting a
    flaky read of the agent's version throw out of the step would discard the whole
    run to report a fact that is, at worst, a caveat on the chart.
    """

    class Broken:
        async def get_version(self):
            raise RuntimeError("connection refused")

        async def get_workspace(self):  # pragma: no cover
            raise NotImplementedError

    store = RecordingStore(make_spec(total_steps=1, steps_per_epoch=1, workspace_version="cfg-1"),
                           make_items(2), make_items(2, "val"))
    Scores({}).install(monkeypatch, store)
    install_preflight(monkeypatch)
    install_update(monkeypatch)

    status, events = await engine.run_optimization(
        store.spec.id, store=store, seams=_SeamsWith(Broken()),
        publish=_collect([]), cancel_event=None,
    ), None
    assert status == "completed"
    assert store.step(1)["workspace_version"] is None


@pytest.mark.asyncio
async def test_no_workspace_seam_means_no_version_and_no_complaint(monkeypatch):
    """`include_workspace` is opt-in, and a run must not depend on it being on.

    Recording an empty string instead of nothing would make every step look like
    it disagreed with the run's pinned version, and the overview would warn about
    drift on every run that never probed.
    """
    store = RecordingStore(make_spec(total_steps=1, steps_per_epoch=1, workspace_version="cfg-1"),
                           make_items(2), make_items(2, "val"))
    Scores({}).install(monkeypatch, store)
    install_preflight(monkeypatch)
    install_update(monkeypatch)

    await run(store, monkeypatch)

    assert store.step(1)["workspace_version"] is None


def _collect(sink):
    async def publish(event):
        sink.append(event)

    return publish


@pytest.mark.asyncio
async def test_an_answer_already_in_the_skill_is_not_re_counted_every_step(monkeypatch):
    """The count is what *this* step added, measured against the skill it started from.

    A leaked line stays in the skill for the rest of the run. Searching the whole
    candidate instead of the added lines would report it again on every later
    step, so the overview would name six steps for one mistake and the reader
    would have no way to find the one that made it. It also has to agree with
    Part 2, which diffs against the parent and marks only added lines.
    """
    leaked = "The Northwind Q2 balance is $42,180.00."
    seeded = {"billing/SKILL.md": f"# Billing\n\nIf asked: {leaked}\n"}

    def fake_update(*, files, skill_dir, **kwargs):
        candidate = dict(files)
        entry = f"{skill_dir}/SKILL.md"
        candidate[entry] = candidate.get(entry, "") + "Quote the period as well.\n"
        return UpdateOutcome(
            files=candidate, patch={"reasoning": "stubbed", "edits": []}, reports=[],
            minibatches=[], n_edits_merged=1, n_edits_ranked=1, n_edits_applied=1,
            n_edits_skipped=0, edit_summary="stubbed", tokens={},
        )

    train = [
        Item(item_key="set:t0", question="q0", ground_truth_response=leaked,
             ground_truth_reasoning="r", ordinal=0),
    ]
    store = RecordingStore(
        make_spec(total_steps=1, steps_per_epoch=1, initial_skill=dict(seeded)),
        train, make_items(2, "val"),
    )
    Scores({}).install(monkeypatch, store)
    install_preflight(monkeypatch)
    monkeypatch.setattr(engine, "run_update_stage", fake_update)

    await run(store, monkeypatch)

    assert store.step(1)["n_answer_leaks"] == 0


# --- The slow update, at the epoch boundary ---------------------------------
#
# Upstream's longitudinal pass: at the end of an epoch, compare the *same*
# samples under the previous epoch's skill and this one's, and write free-form
# guidance into a protected block that step-level edits may not touch. It is
# off unless the run asks for it.


class _RecordingOptimizer:
    """A fake optimizer that answers the slow-update contract and counts calls."""

    model_name = "fake"

    def __init__(self, content="Prefer stating the period before the figure."):
        self.content = content
        self.stages: list[str] = []
        self.prompts: list[str] = []

    def chat_optimizer(self, system, user, max_completion_tokens=0, retries=0,
                       stage="optimizer", timeout=None):
        self.stages.append(stage)
        self.prompts.append(user)
        if stage == "slow_update":
            return json.dumps({
                "reasoning": "regressions clustered on refunds",
                "slow_update_content": self.content,
            }), {"calls": 1}
        if stage == "meta_skill":
            return json.dumps({"meta_skill_content": "Edit one rule at a time."}), {"calls": 1}
        return json.dumps({"selected_indices": [0]}), {"calls": 1}


def _slow_spec(**over):
    config = {"seed": 3, "slow_update": True}
    config.update(over.pop("config", {}))
    return make_spec(config=config, **over)


async def _run_with_optimizer(store, monkeypatch, optimizer):
    return await engine.run_optimization(
        store.spec.id, store=store, seams=_SeamsWith(None, optimizer=optimizer),
        publish=_collect([]), cancel_event=None,
    )


@pytest.mark.asyncio
async def test_the_slow_update_is_off_unless_the_run_asks_for_it(monkeypatch):
    """Default off means the code path is provably not entered.

    A feature that ships disabled has to cost nothing when disabled — no extra
    optimizer call, no change to the skill, nothing on the step rows. "Off" that
    still runs the comparison and throws the answer away is the expensive kind
    of off.
    """
    store = RecordingStore(make_spec(total_steps=2, steps_per_epoch=1, num_epochs=2),
                           make_items(2), make_items(2, "val"))
    Scores({}).install(monkeypatch, store)
    install_preflight(monkeypatch)
    install_update(monkeypatch)
    optimizer = _RecordingOptimizer()

    await _run_with_optimizer(store, monkeypatch, optimizer)

    assert "slow_update" not in optimizer.stages
    assert not [s for s in store.skills if s["kind"] == "slow_update"]


@pytest.mark.asyncio
async def test_an_epoch_boundary_writes_guidance_into_the_protected_block(monkeypatch):
    """The whole feature, end to end, on the smallest run that has a boundary.

    Two epochs of one step each. The first epoch's accepted candidate and the
    second's are two different skills measured on the *same* validation split,
    which is exactly the comparison upstream wants and the only fixed sample set
    this loop produces.
    """
    store = RecordingStore(
        _slow_spec(total_steps=2, steps_per_epoch=1, num_epochs=2),
        make_items(2), make_items(2, "val"),
    )
    Scores({
        (0, "val"): (2, 0),      # baseline 0.0
        (1, "train"): (2, 1),
        (1, "val"): (2, 1),      # accepted, 0.5
        (2, "train"): (2, 1),
        (2, "val"): (2, 2),      # accepted, 1.0
    }).install(monkeypatch, store)
    install_preflight(monkeypatch)
    install_update(monkeypatch)
    optimizer = _RecordingOptimizer()

    await _run_with_optimizer(store, monkeypatch, optimizer)

    assert "slow_update" in optimizer.stages
    snapshot = next(s for s in store.skills if s["kind"] == "slow_update")
    entry = snapshot["files"]["billing/SKILL.md"]
    assert SLOW_UPDATE_START in entry and SLOW_UPDATE_END in entry
    assert "Prefer stating the period before the figure." in entry


@pytest.mark.asyncio
async def test_an_epoch_that_changed_nothing_is_not_compared_against_itself(monkeypatch):
    """Two identical skills produce a comparison with nothing in it.

    If every candidate in an epoch was rejected, the skill at the end of it is
    the skill it started with. Running the slow update anyway spends a call on
    the largest model configured to be told that nothing moved, and invites it
    to write guidance about a change that did not happen.
    """
    store = RecordingStore(
        _slow_spec(total_steps=2, steps_per_epoch=1, num_epochs=2),
        make_items(2), make_items(2, "val"),
    )
    Scores({
        (0, "val"): (2, 2),      # baseline 1.0 — nothing can beat it
        (1, "train"): (2, 1),
        (1, "val"): (2, 1),      # rejected
        (2, "train"): (2, 1),
        (2, "val"): (2, 1),      # rejected
    }).install(monkeypatch, store)
    install_preflight(monkeypatch)
    install_update(monkeypatch)
    optimizer = _RecordingOptimizer()

    await _run_with_optimizer(store, monkeypatch, optimizer)

    assert "slow_update" not in optimizer.stages


@pytest.mark.asyncio
async def test_the_comparison_is_the_validation_split_measured_twice(monkeypatch):
    """Not the training minibatch, which is a different draw of questions.

    A longitudinal comparison needs the *same* samples on both sides. Training
    batches are reshuffled every epoch, so comparing them would attribute the
    difference between two sets of questions to the difference between two
    skills — which is the one thing this pass exists to measure.
    """
    store = RecordingStore(
        _slow_spec(total_steps=2, steps_per_epoch=1, num_epochs=2),
        make_items(4), make_items(3, "val"),
    )
    Scores({
        (0, "val"): (3, 0),
        (1, "train"): (4, 2), (1, "val"): (3, 1),
        (2, "train"): (4, 2), (2, "val"): (3, 3),
    }).install(monkeypatch, store)
    install_preflight(monkeypatch)
    install_update(monkeypatch)
    optimizer = _RecordingOptimizer()

    await _run_with_optimizer(store, monkeypatch, optimizer)

    prompt = optimizer.prompts[optimizer.stages.index("slow_update")]
    for item in store._items["val"]:
        assert item.item_key in prompt
    assert not any(item.item_key in prompt for item in store._items["train"])


@pytest.mark.asyncio
async def test_a_slow_update_that_fails_leaves_the_run_and_the_skill_alone(monkeypatch):
    """It is an enrichment, not a step. An hour of agent calls is already spent.

    Ending the run because a single optional optimizer call raised would discard
    every measurement taken so far to report the failure of something that is
    off by default.
    """

    class Broken(_RecordingOptimizer):
        def chat_optimizer(self, system, user, stage="optimizer", **kwargs):
            self.stages.append(stage)
            if stage == "slow_update":
                raise RuntimeError("the optimizer endpoint is down")
            return json.dumps({"selected_indices": [0]}), {"calls": 1}

    store = RecordingStore(
        _slow_spec(total_steps=2, steps_per_epoch=1, num_epochs=2),
        make_items(2), make_items(2, "val"),
    )
    Scores({
        (0, "val"): (2, 0),
        (1, "train"): (2, 1), (1, "val"): (2, 1),
        (2, "train"): (2, 1), (2, "val"): (2, 2),
    }).install(monkeypatch, store)
    install_preflight(monkeypatch)
    install_update(monkeypatch)

    status = await _run_with_optimizer(store, monkeypatch, Broken())

    assert status == "completed"
    assert not [s for s in store.skills if s["kind"] == "slow_update"]


@pytest.mark.asyncio
async def test_the_guidance_survives_into_the_next_epoch_and_is_offered_back(monkeypatch):
    """The pass is longitudinal in both directions.

    Upstream shows the previous guidance to the next slow update and asks it to
    judge whether that guidance worked before writing the replacement. Dropping
    it turns a running memory into a series of unrelated one-shot opinions.
    """
    store = RecordingStore(
        _slow_spec(total_steps=3, steps_per_epoch=1, num_epochs=3),
        make_items(2), make_items(2, "val"),
    )
    Scores({
        (0, "val"): (2, 0),
        (1, "train"): (2, 1), (1, "val"): (2, 1),
        (2, "train"): (2, 1), (2, "val"): (2, 2),
        (3, "train"): (2, 2), (3, "val"): (2, 2),
    }).install(monkeypatch, store)
    install_preflight(monkeypatch)
    install_update(monkeypatch)
    optimizer = _RecordingOptimizer()

    await _run_with_optimizer(store, monkeypatch, optimizer)

    slow_prompts = [
        p for p, s in zip(optimizer.prompts, optimizer.stages) if s == "slow_update"
    ]
    assert len(slow_prompts) >= 2
    # Asserting on the text alone would prove nothing: by the second boundary the
    # guidance is *also* inside the skill, which the same prompt quotes in full.
    # The sentinel upstream writes when it has no previous guidance is the only
    # thing that distinguishes "carried forward" from "visible by accident".
    first_time = "(No previous guidance — this is the first slow update.)"
    assert first_time in slow_prompts[0]
    assert first_time not in slow_prompts[1]
    assert "Prefer stating the period before the figure." in slow_prompts[1]


@pytest.mark.asyncio
async def test_the_meta_skill_reaches_the_analyst_on_later_steps(monkeypatch):
    """Optimizer-side memory that nothing reads is memory that does not exist.

    The meta skill is never written into the skill — it is advice to the *editor*
    about how to edit, carried from one epoch into the next and shown to the
    analyst alongside the failures. It is produced at a boundary and consumed by
    `run_update_stage`, and those are two different modules: exactly the shape of
    gap where a value gets computed, stored on the state object, and then never
    passed on.
    """
    seen: list[str] = []

    def fake_update(*, files, skill_dir, meta_skill_context="", **kwargs):
        seen.append(meta_skill_context)
        candidate = dict(files)
        entry = f"{skill_dir}/SKILL.md"
        candidate[entry] = candidate.get(entry, "") + f"rule {len(seen)}\n"
        return UpdateOutcome(
            files=candidate, patch={"reasoning": "stubbed", "edits": []}, reports=[],
            minibatches=[], n_edits_merged=1, n_edits_ranked=1, n_edits_applied=1,
            n_edits_skipped=0, edit_summary="stubbed", tokens={},
        )

    store = RecordingStore(
        make_spec(total_steps=2, steps_per_epoch=1, num_epochs=2,
                  config={"seed": 3, "meta_skill": True}),
        make_items(2), make_items(2, "val"),
    )
    Scores({
        (0, "val"): (2, 0),
        (1, "train"): (2, 1), (1, "val"): (2, 1),
        (2, "train"): (2, 1), (2, "val"): (2, 2),
    }).install(monkeypatch, store)
    install_preflight(monkeypatch)
    monkeypatch.setattr(engine, "run_update_stage", fake_update)

    await _run_with_optimizer(store, monkeypatch, _RecordingOptimizer())

    # Step 1 runs before any boundary, so it has nothing to be told.
    assert seen[0] == ""
    assert "Edit one rule at a time." in seen[1]


@pytest.mark.asyncio
async def test_the_meta_skill_alone_never_edits_the_skill(monkeypatch):
    """It is advice to the optimizer, not content for the agent.

    Writing it into `SKILL.md` would put the optimizer's notes about its own
    editing habits in front of the agent at answer time, and ship them inside
    the downloaded zip.
    """
    store = RecordingStore(
        make_spec(total_steps=2, steps_per_epoch=1, num_epochs=2,
                  config={"seed": 3, "meta_skill": True}),
        make_items(2), make_items(2, "val"),
    )
    Scores({
        (0, "val"): (2, 0),
        (1, "train"): (2, 1), (1, "val"): (2, 1),
        (2, "train"): (2, 1), (2, "val"): (2, 2),
    }).install(monkeypatch, store)
    install_preflight(monkeypatch)
    install_update(monkeypatch)

    await _run_with_optimizer(store, monkeypatch, _RecordingOptimizer())

    assert not [s for s in store.skills if s["kind"] == "slow_update"]
    assert all(
        "Edit one rule at a time." not in "".join(s["files"].values())
        for s in store.skills
    )


@pytest.mark.asyncio
async def test_the_boundary_compares_the_two_epochs_either_side_of_it(monkeypatch):
    """Not "this epoch versus the baseline", which is a different question.

    The pass is Markov: adjacent epochs only. Comparing epoch 3 against step 0
    would fold three epochs of change into one comparison and ask the optimizer
    to write guidance about a journey rather than about the step it just took —
    and it would say the same thing about improvements it had already commented
    on twice.
    """
    seen: list[tuple[int, int]] = []
    real = engine.run_epoch_boundary

    def spy(*, results_prev, results_curr, **kwargs):
        seen.append((len(results_prev), len(results_curr)))
        # Tag each side so the assertion can tell which step it came from.
        return real(results_prev=results_prev, results_curr=results_curr, **kwargs)

    store = RecordingStore(
        _slow_spec(total_steps=2, steps_per_epoch=1, num_epochs=2),
        make_items(2), make_items(4, "val"),
    )
    Scores({
        (0, "val"): (4, 0),
        (1, "train"): (2, 1), (1, "val"): (4, 1),
        (2, "train"): (2, 1), (2, "val"): (4, 2),
    }).install(monkeypatch, store)
    install_preflight(monkeypatch)
    install_update(monkeypatch)
    monkeypatch.setattr(engine, "run_epoch_boundary", spy)

    calls: list[tuple] = []
    original_load = store.load_val_results

    async def watched(run_id, step_no):
        calls.append(step_no)
        return await original_load(run_id, step_no)

    store.load_val_results = watched
    await _run_with_optimizer(store, monkeypatch, _RecordingOptimizer())

    # Epoch 1 compares the baseline with step 1; epoch 2 compares step 1 with 2.
    assert calls == [0, 1, 1, 2]


@pytest.mark.asyncio
async def test_the_skill_the_boundary_wrote_is_what_the_next_step_edits(monkeypatch):
    """The guidance has to be *in* the skill the run carries, not only in a row.

    Recording the snapshot and then continuing from the pre-boundary skill would
    leave a snapshot nothing produced: the block would appear in the download and
    in Part 2's base, and vanish from every later candidate — so the next
    accepted step would read as having deleted it.
    """
    seen: list[dict] = []

    def fake_update(*, files, skill_dir, **kwargs):
        seen.append(dict(files))
        candidate = dict(files)
        entry = f"{skill_dir}/SKILL.md"
        candidate[entry] = candidate.get(entry, "") + f"rule {len(seen)}\n"
        return UpdateOutcome(
            files=candidate, patch={"reasoning": "s", "edits": []}, reports=[],
            minibatches=[], n_edits_merged=1, n_edits_ranked=1, n_edits_applied=1,
            n_edits_skipped=0, edit_summary="s", tokens={},
        )

    store = RecordingStore(
        _slow_spec(total_steps=2, steps_per_epoch=1, num_epochs=2),
        make_items(2), make_items(2, "val"),
    )
    Scores({
        (0, "val"): (2, 0),
        (1, "train"): (2, 1), (1, "val"): (2, 1),
        (2, "train"): (2, 1), (2, "val"): (2, 2),
    }).install(monkeypatch, store)
    install_preflight(monkeypatch)
    monkeypatch.setattr(engine, "run_update_stage", fake_update)

    await _run_with_optimizer(store, monkeypatch, _RecordingOptimizer())

    assert SLOW_UPDATE_START not in seen[0]["billing/SKILL.md"]
    assert SLOW_UPDATE_START in seen[1]["billing/SKILL.md"]


@pytest.mark.asyncio
async def test_the_boundary_waits_for_the_end_of_the_epoch(monkeypatch):
    """Four steps, two per epoch: two boundaries, not four.

    Running it after every step would spend a call on the largest model
    configured four times instead of twice, and would compare a skill against
    itself halfway through an epoch — the mid-epoch mark is the same step as the
    one before it whenever the middle step was rejected.
    """
    store = RecordingStore(
        _slow_spec(total_steps=4, steps_per_epoch=2, num_epochs=2),
        make_items(4), make_items(4, "val"),
    )
    # Four validation questions so every step can beat the last: with two, the
    # score saturates at 1.0 by step 2 and the second epoch accepts nothing —
    # which correctly produces *one* boundary and would hide the bug this is for.
    Scores({
        (0, "val"): (4, 0),
        (1, "train"): (2, 1), (1, "val"): (4, 1),
        (2, "train"): (2, 1), (2, "val"): (4, 2),
        (3, "train"): (2, 1), (3, "val"): (4, 3),
        (4, "train"): (2, 1), (4, "val"): (4, 4),
    }).install(monkeypatch, store)
    install_preflight(monkeypatch)
    install_update(monkeypatch)
    optimizer = _RecordingOptimizer()

    await _run_with_optimizer(store, monkeypatch, optimizer)

    assert optimizer.stages.count("slow_update") == 2
