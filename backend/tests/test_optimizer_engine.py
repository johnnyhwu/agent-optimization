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
import uuid

import pytest

from app.optimizer import engine
from app.optimizer.store import Item, ResultRow, RolloutSummary, RunSpec
from app.optimizer.update import MinibatchRecord, UpdateOutcome

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


class Scores:
    """Scripts what each rollout returns, keyed by `(step_no, split)`.

    Every test here is really a statement about which skill was measured when,
    so the script is the test's premise and the assertions are about what the
    loop did with it.
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
            n, correct = self.script.get((step_no, split), self.default)
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
        entry = f"{skill_dir}/SKILL.md"
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


def install_preflight(monkeypatch, *, activated=True):
    async def fake_probe(*args, **kwargs):
        row = ResultRow(item_key="probe", correlation_id="p", status="done")
        row.activated = activated
        row.detector_hit = "tool_path" if activated else "none"
        row.skills_read = ["billing"] if activated else []
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


@pytest.mark.asyncio
async def test_a_batch_that_mostly_failed_is_retried_once(monkeypatch):
    """One retry absorbs a blip; scoring the remainder would poison the run.

    A step measured on 60% of its batch is not a smaller measurement, it is an
    unrepresentative one, and the gate cannot tell. Retrying once is what makes
    a thirty-second outage cost a step instead of a run.
    """
    store = RecordingStore(make_spec(total_steps=1, steps_per_epoch=1),
                           make_items(2), make_items(4, "val"))
    attempts = []

    async def flaky(items, *, skill_files, mode, skill_name, seams, config, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            failed = [ResultRow(item_key=f"k{i}", correlation_id=f"c{i}",
                                status="failed", failure_kind="agent") for i in range(4)]
            return failed
        return make_rows(4, correct=2)

    monkeypatch.setattr(engine, "run_rollout", flaky)
    install_preflight(monkeypatch)
    install_update(monkeypatch)

    status, _ = await run(store, monkeypatch)

    assert len(attempts) >= 2
    assert store.step(0)["retried"] is True
    assert status != "failed"


@pytest.mark.asyncio
async def test_a_second_failed_attempt_ends_the_run_rather_than_guessing(monkeypatch):
    """Twice in a row is not a blip, and there is nothing useful left to do.

    Continuing would mean writing skill edits derived from a batch that mostly
    did not run — and the developer would find out from a chart that looks
    ordinary.
    """
    store = RecordingStore(make_spec(total_steps=2, steps_per_epoch=2),
                           make_items(4), make_items(4, "val"))

    async def always_failing(items, **kwargs):
        return [ResultRow(item_key=f"k{i}", correlation_id=f"c{i}",
                          status="failed", failure_kind="agent") for i in range(4)]

    monkeypatch.setattr(engine, "run_rollout", always_failing)
    install_preflight(monkeypatch)
    install_update(monkeypatch)

    status, events = await run(store, monkeypatch)

    assert status == "failed"
    assert store.final["error_message"]
    assert events[-1]["type"] == "run_completed"


# --- Pre-flight -------------------------------------------------------------


@pytest.mark.asyncio
async def test_routing_refuses_to_start_when_the_skill_cannot_be_seen(monkeypatch):
    """A routing run with no detector signal cannot measure its own outcome.

    Its gate compares activation rates. If nothing can observe activation, every
    comparison is against `None` and the run degenerates into an accuracy-only
    run that is *also* forbidden from editing the body — an hour spent, nothing
    learned. Better to say so in the first ten seconds.
    """
    store = RecordingStore(make_spec(mode="routing"), make_items(4), make_items(4, "val"))
    Scores({}).install(monkeypatch, store)
    install_preflight(monkeypatch, activated=False)
    install_update(monkeypatch)

    status, events = await run(store, monkeypatch)

    assert status == "failed"
    assert not store.steps
    preflight = next(e for e in events if e["type"] == "preflight")
    assert preflight["ok"] is False


@pytest.mark.asyncio
async def test_isolated_warns_about_a_silent_detector_and_carries_on(monkeypatch):
    """Isolated mode does not need the detector to reach a verdict.

    Its gate is accuracy, and accuracy is measurable whether or not the agent
    announces which file it read. Aborting here would block the common case —
    an agent whose traces do not name skill paths — from using the feature at
    all, so the honest move is to say the activation column will read 'unknown'.
    """
    store = RecordingStore(make_spec(mode="isolated", total_steps=1, steps_per_epoch=1),
                           make_items(2), make_items(2, "val"))
    Scores({(0, "val"): (2, 1), (1, "train"): (2, 1), (1, "val"): (2, 2)}).install(
        monkeypatch, store
    )
    install_preflight(monkeypatch, activated=False)
    install_update(monkeypatch)

    status, events = await run(store, monkeypatch)

    preflight = next(e for e in events if e["type"] == "preflight")
    assert preflight["ok"] is False
    assert status == "completed"
    assert store.steps


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
    assert "### Trajectory 1" in store.minibatches[0]["prompt_user"]
    assert store.minibatches[0]["raw_output"]["failure_summary"]
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
