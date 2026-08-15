import test from "node:test";
import assert from "node:assert/strict";

import {
  applyEvent,
  emptySteps,
  replaceSteps,
  stepList,
  stepProgress,
} from "./optimize_steps.js";

// The failure this module exists to prevent is silent: a run streams for an
// hour and the chart beside it is a still photograph of the moment the page
// loaded. Nothing throws, nothing is empty, and every number on screen is
// wrong. So the tests below are mostly about *time* — events arriving late,
// twice, out of order, or after the refetch that was supposed to settle things.

const rollout = (stepNo, split, over = {}) => ({
  step_no: stepNo,
  split,
  hard: 0.5,
  soft: 0.6,
  activation_rate: 0.9,
  n_items: 10,
  n_scored: 10,
  n_agent_error: 0,
  n_judge_error: 0,
  latency_min_ms: 100,
  latency_p50_ms: 200,
  latency_max_ms: 900,
  ...over,
});

const feed = (state, events) =>
  events.reduce((acc, [type, data]) => applyEvent(acc, type, data), state);

test("a step the snapshot never carried is built from its events alone", () => {
  // The case the old code could not represent at all: the page opened before
  // step 3 existed, so no refetch has ever mentioned it.
  const state = feed(emptySteps(), [
    ["step_started", { step_no: 3, epoch_no: 2, step_in_epoch: 1, phase: "rollout" }],
    ["rollout_done", rollout(3, "train", { hard: 0.4 })],
    ["update_done", { step_no: 3, n_edits_applied: 2, lines_added: 9, lines_removed: 3 }],
    ["rollout_done", rollout(3, "val", { hard: 0.7 })],
    ["gate_done", { step_no: 3, action: "accept_new_best", best_score: 0.7 }],
  ]);

  const [step] = stepList(state);
  assert.equal(step.step_no, 3);
  assert.equal(step.epoch_no, 2);
  assert.equal(step.train_hard, 0.4);
  assert.equal(step.val_hard, 0.7);
  assert.equal(step.lines_added, 9);
  assert.equal(step.gate_action, "accept_new_best");
  assert.equal(step.status, "done");
});

test("train and val land in separate fields — they measure two different skills", () => {
  const state = feed(emptySteps(), [
    ["rollout_done", rollout(1, "train", { hard: 0.2, soft: 0.3, n_items: 8 })],
    ["rollout_done", rollout(1, "val", { hard: 0.9, soft: 0.95, n_items: 40 })],
  ]);

  const [step] = stepList(state);
  assert.equal(step.train_hard, 0.2);
  assert.equal(step.val_hard, 0.9);
  assert.equal(step.train_n_items, 8);
  assert.equal(step.val_n_items, 40);
  // The bare names must not survive the merge; a `hard` on the row would be
  // whichever rollout happened to arrive last.
  assert.equal(step.hard, undefined);
  assert.equal(step.split, undefined);
});

test("an event never blanks a field another event already filled", () => {
  // Rule 1. `gate_done` says nothing about the rollouts, and must not imply
  // anything about them either.
  const state = feed(emptySteps(), [
    ["rollout_done", rollout(1, "val", { hard: 0.8 })],
    ["gate_done", { step_no: 1, action: "reject", reject_reason: "no_improvement" }],
  ]);

  const [step] = stepList(state);
  assert.equal(step.val_hard, 0.8);
  assert.equal(step.val_n_items, 10);
  assert.equal(step.gate_reject_reason, "no_improvement");
});

test("a replayed step_started cannot walk a finished step back to running", () => {
  // The hub replays on resync, and `step_started` is the event most likely to
  // be seen twice. A row that flips back to `running` would take its badge and
  // its em-dashes with it.
  const state = feed(emptySteps(), [
    ["step_started", { step_no: 2, epoch_no: 1, phase: "rollout" }],
    ["gate_done", { step_no: 2, action: "accept" }],
    ["step_started", { step_no: 2, epoch_no: 1, phase: "rollout" }],
  ]);

  assert.equal(stepList(state)[0].status, "done");
  assert.equal(stepList(state)[0].gate_action, "accept");
});

test("a duplicated event is idempotent", () => {
  const once = feed(emptySteps(), [["rollout_done", rollout(1, "val")]]);
  const twice = feed(once, [["rollout_done", rollout(1, "val")]]);
  assert.deepEqual(stepList(twice), stepList(once));
});

test("out-of-order arrivals are sorted by step number, not by arrival", () => {
  // The x-axis is step number. A chart following insertion order zigzags.
  const state = feed(emptySteps(), [
    ["rollout_done", rollout(4, "val")],
    ["rollout_done", rollout(1, "val")],
    ["rollout_done", rollout(0, "val")],
    ["rollout_done", rollout(2, "val")],
  ]);
  assert.deepEqual(stepList(state).map((s) => s.step_no), [0, 1, 2, 4]);
});

test("a refetch replaces the map, and is the only thing that can remove a step", () => {
  // Rule 2. This is the bug that survived the run finishing: the stale live
  // steps outranked the authoritative refetch forever.
  const streamed = feed(emptySteps(), [
    ["rollout_done", rollout(0, "val", { hard: 0.1 })],
    ["rollout_done", rollout(1, "val", { hard: 0.2 })],
  ]);
  const settled = replaceSteps(streamed, [
    { step_no: 0, status: "done", val_hard: 0.15 },
    { step_no: 1, status: "done", val_hard: 0.25 },
    { step_no: 2, status: "done", val_hard: 0.35 },
  ]);

  assert.equal(stepList(settled).length, 3);
  assert.equal(stepList(settled)[0].val_hard, 0.15);
  assert.equal(stepList(settled)[2].val_hard, 0.35);
  // And nothing of the streamed row leaks through the replacement.
  assert.equal(stepList(settled)[0].val_n_items, undefined);
});

test("events after a refetch keep merging onto it", () => {
  const settled = replaceSteps(emptySteps(), [
    { step_no: 0, status: "done", val_hard: 0.15 },
  ]);
  const after = feed(settled, [
    ["step_started", { step_no: 1, epoch_no: 1, phase: "rollout" }],
    ["rollout_done", rollout(1, "train", { hard: 0.4 })],
  ]);

  assert.deepEqual(stepList(after).map((s) => s.step_no), [0, 1]);
  assert.equal(stepList(after)[0].val_hard, 0.15);
  assert.equal(stepList(after)[1].train_hard, 0.4);
});

test("a retried rollout keeps the flag that explains its noise", () => {
  const state = feed(emptySteps(), [
    ["rollout_retry", { step_no: 2, split: "val", reason: "too_many_errors" }],
    ["rollout_done", rollout(2, "val")],
  ]);
  assert.equal(stepList(state)[0].retried, true);
});

test("an event with no step number changes no rows", () => {
  const state = feed(emptySteps(), [["rollout_done", rollout(1, "val")]]);
  const after = applyEvent(state, "preflight", { ok: true });
  assert.deepEqual(stepList(after), stepList(state));
});

test("run_completed clears what the run is doing, without touching the rows", () => {
  const state = feed(emptySteps(), [
    ["step_started", { step_no: 1, epoch_no: 1, phase: "rollout" }],
  ]);
  assert.ok(state.activity);

  const done = applyEvent(state, "run_completed", { status: "completed" });
  assert.equal(done.activity, null);
  assert.equal(stepList(done).length, 1);
});

test("the activity names the step and the stage it is in", () => {
  // The baseline is one validation rollout; every other step opens on training.
  let state = applyEvent(emptySteps(), "step_started", { step_no: 0, phase: "baseline" });
  assert.deepEqual(
    { stepNo: state.activity.stepNo, phase: state.activity.phase },
    { stepNo: 0, phase: "rollout_val" },
  );

  state = applyEvent(state, "step_started", { step_no: 1, epoch_no: 1, phase: "rollout" });
  assert.equal(state.activity.phase, "rollout_train");

  state = applyEvent(state, "gate_done", { step_no: 4, action: "reject" });
  assert.equal(state.activity.stepNo, 4);
  assert.equal(state.activity.phase, "gate");
  assert.match(state.activity.note, /rejected/);
});

test("a rollout reports how many of its questions are answered", () => {
  // The whole point of the event: `rollout_done` fires once, at the end, and a
  // rollout is the longest thing in a step. Between the two the header had
  // nothing to say for minutes at a time.
  let state = applyEvent(emptySteps(), "step_started", { step_no: 2, epoch_no: 1 });
  assert.equal(state.activity.done, undefined);

  state = applyEvent(state, "rollout_progress", {
    step_no: 2, split: "train", done: 3, total: 8, attempt: 1,
  });
  assert.deepEqual(
    {
      phase: state.activity.phase,
      done: state.activity.done,
      total: state.activity.total,
      note: state.activity.note,
    },
    { phase: "rollout_train", done: 3, total: 8, note: null },
  );

  // A retry restarts the count, so it says so rather than appearing to go
  // backwards for no reason.
  state = applyEvent(state, "rollout_progress", {
    step_no: 2, split: "train", done: 1, total: 8, attempt: 2,
  });
  assert.equal(state.activity.done, 1);
  assert.match(state.activity.note, /retrying/);

  // Progress carries no step fields, so it must not invent a row or disturb one.
  assert.equal(stepList(state).length, 1);
  assert.equal(stepList(state)[0].step_no, 2);
});

test("each stage hands over to the one the engine actually does next", () => {
  // Every event reports a *completed* stage, so the activity it produces has to
  // name what follows it — otherwise the strip lags one stage behind the run
  // for the whole of it.
  const after = (type, data) => applyEvent(emptySteps(), type, data).activity.phase;

  assert.equal(after("rollout_done", { step_no: 1, split: "train" }), "reflect");
  assert.equal(after("reflect_done", { step_no: 1, n_minibatches: 2 }), "update");
  assert.equal(after("update_done", { step_no: 1, n_edits_applied: 2 }), "rollout_val");
  assert.equal(after("rollout_done", { step_no: 1, split: "val" }), "gate");
});

test("the baseline finishes on its validation rollout, having no gate to end it", () => {
  // `_run_baseline` publishes step_started then one rollout_done, and never a
  // gate_done. A client building step 0 from the stream alone would otherwise
  // show the chart's first point as running for the whole hour.
  const state = applyEvent(emptySteps(), "rollout_done", {
    step_no: 0,
    split: "val",
    hard: 0.42,
  });
  assert.equal(stepList(state)[0].status, "done");
  assert.equal(stepList(state)[0].val_hard, 0.42);
});

test("a training rollout does not finish the step it belongs to", () => {
  // Only step 0 ends on a rollout. Step 3's train rollout is its first of two.
  const state = applyEvent(emptySteps(), "rollout_done", { step_no: 3, split: "train" });
  assert.equal(stepList(state)[0].status, "running");
});

test("progress counts the baseline once, on both sides of the screen", () => {
  // The rail said 4/12 while the panel said 5/13 for the same run. Both now
  // count finished steps against the same denominator — and the panel must not
  // count the step currently in flight, or it runs one ahead of the rail.
  const steps = [
    { step_no: 0, status: "done" },
    { step_no: 1, status: "done" },
    { step_no: 2, status: "done" },
    { step_no: 3, status: "running" },
  ];
  assert.equal(stepProgress({ total_steps: 12, steps_done: 3 }, steps).label, "3/13");
  assert.equal(stepProgress({ total_steps: 12, steps_done: 3 }, null).label, "3/13");
});

test("progress declines to invent a denominator it does not have", () => {
  // A pending run has no `total_steps` yet, and `total_steps + 1` rendered
  // "NaN" in the middle of the run header.
  const progress = stepProgress({ total_steps: null }, [{ step_no: 0, status: "done" }]);
  assert.equal(progress.total, null);
  assert.equal(progress.label, "1");
  assert.ok(!progress.label.includes("NaN"));
});
