import test from "node:test";
import assert from "node:assert/strict";

import { applyEvent, emptySteps, replaceSteps, stepList } from "./optimize_steps.js";

// The seam that broke twice, tested with the bytes the server actually sends.
//
// The unit tests above hand the reducer parsed objects, so they cannot see a
// wiring mistake between the socket and the reducer — and that is exactly where
// both bugs lived. The overview subscribed to two of the seven step events, and
// it read `e.step_no` off the SSE frame instead of `JSON.parse(e.data).step_no`,
// so the caption above the chart said "step undefined · undefined" for the
// whole run while the chart itself never moved.
//
// This reimplements the frame parser from `api.js#openStream` — it cannot be
// imported here, since api.js reaches for `window` through app_config — and
// drives it with real `sse-starlette` output, CRLF terminators included.

function parseFrames(wire, emit) {
  let buffer = wire;
  const danglingCR = buffer.endsWith("\r");
  let pending = (danglingCR ? buffer.slice(0, -1) : buffer).replace(/\r\n|\r/g, "\n");
  let split;
  while ((split = pending.indexOf("\n\n")) !== -1) {
    const frame = pending.slice(0, split);
    pending = pending.slice(split + 2);
    let name = "message";
    const data = [];
    for (const line of frame.split("\n")) {
      if (line.startsWith("event:")) name = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
    }
    if (data.length) emit(name, data.join("\n"));
  }
}

const frame = (event, payload) =>
  `event: ${event}\r\ndata: ${JSON.stringify(payload)}\r\n\r\n`;

// Registered exactly as RunPanel registers them, including the parse wrapper.
function subscribe() {
  let state = emptySteps();
  const parse = (fn) => (e) => {
    let payload;
    try { payload = JSON.parse(e.data); } catch { return; }
    fn(payload);
  };
  const handlers = new Map();
  const on = (name, fn) => handlers.set(name, fn);

  on("snapshot", parse((d) => { state = replaceSteps(state, d.steps || []); }));
  for (const name of [
    "step_started", "rollout_done", "rollout_progress",
    "reflect_done", "update_done", "gate_done", "slow_update_done",
  ]) {
    on(name, parse((d) => { state = applyEvent(state, name, d); }));
  }
  on("run_completed", parse((d) => { state = applyEvent(state, "run_completed", d); }));

  return {
    emit: (name, raw) => handlers.get(name)?.({ data: raw }),
    get state() { return state; },
  };
}

test("a run's real wire output assembles the chart the page draws", () => {
  const sub = subscribe();
  // Exactly what the engine publishes, in order, for a one-step run.
  const wire = [
    frame("snapshot", { status: "running", total_steps: 1, steps: [] }),
    frame("step_started", { step_no: 0, epoch_no: 0, phase: "baseline" }),
    frame("rollout_done", {
      step_no: 0, split: "val", hard: 0.5, soft: 0.55, activation_rate: 1,
      n_items: 4, n_scored: 4, n_agent_error: 0, n_judge_error: 0,
      latency_min_ms: 10, latency_p50_ms: 20, latency_max_ms: 30,
    }),
    frame("step_started", { step_no: 1, epoch_no: 1, step_in_epoch: 1, phase: "rollout" }),
    frame("rollout_done", {
      step_no: 1, split: "train", hard: 0.25, soft: 0.3, activation_rate: 1,
      n_items: 4, n_scored: 4, n_agent_error: 0, n_judge_error: 0,
      latency_min_ms: 10, latency_p50_ms: 20, latency_max_ms: 30,
    }),
    frame("reflect_done", { step_no: 1, n_minibatches: 2, n_patches: 3 }),
    frame("update_done", { step_no: 1, n_edits_applied: 3, lines_added: 12, lines_removed: 4 }),
    frame("rollout_done", {
      step_no: 1, split: "val", hard: 0.75, soft: 0.8, activation_rate: 1,
      n_items: 4, n_scored: 4, n_agent_error: 0, n_judge_error: 0,
      latency_min_ms: 10, latency_p50_ms: 20, latency_max_ms: 30,
    }),
    frame("gate_done", {
      step_no: 1, action: "accept_new_best", reject_reason: null,
      candidate_score: 0.75, current_score: 0.75, best_score: 0.75, from_cache: false,
    }),
  ].join("");

  parseFrames(wire, sub.emit);
  const steps = stepList(sub.state);

  // Before this change the snapshot dropped every step and no other event was
  // even subscribed to, so this list was empty for the whole run.
  assert.equal(steps.length, 2);
  assert.deepEqual(steps.map((s) => s.step_no), [0, 1]);

  const [baseline, one] = steps;
  assert.equal(baseline.val_hard, 0.5);
  assert.equal(baseline.status, "done");
  assert.equal(one.train_hard, 0.25);
  assert.equal(one.val_hard, 0.75);
  assert.equal(one.lines_added, 12);
  assert.equal(one.gate_action, "accept_new_best");
  assert.equal(one.status, "done");
});

test("what the run is doing names a real step, not undefined", () => {
  // The exact symptom: `e.step_no` off an unparsed frame is undefined, and the
  // header rendered that string at the user. The activity is structured now
  // rather than a sentence, so the guard is that the step number is a number.
  const sub = subscribe();
  parseFrames(frame("gate_done", { step_no: 4, action: "reject" }), sub.emit);

  assert.ok(sub.state.activity, "an activity should have been produced");
  assert.equal(sub.state.activity.stepNo, 4);
  assert.equal(sub.state.activity.phase, "gate");
});

test("a rollout_progress frame off the wire carries its counts through", () => {
  // Same parse path as every other frame, and the one event whose whole value
  // is the two numbers on it.
  const sub = subscribe();
  parseFrames(
    frame("rollout_progress", { step_no: 2, split: "train", done: 5, total: 8, attempt: 1 }),
    sub.emit,
  );

  assert.deepEqual(
    {
      stepNo: sub.state.activity.stepNo,
      phase: sub.state.activity.phase,
      done: sub.state.activity.done,
      total: sub.state.activity.total,
    },
    { stepNo: 2, phase: "rollout_train", done: 5, total: 8 },
  );
});

test("a frame split across chunk boundaries is not mistaken for two", () => {
  // The reader hands over arbitrary slices; a half-parsed payload must not
  // reach JSON.parse and take the read loop down.
  const sub = subscribe();
  const wire = frame("rollout_done", { step_no: 0, split: "val", hard: 0.9 });
  parseFrames(wire.slice(0, 30), sub.emit);
  assert.equal(stepList(sub.state).length, 0);
  parseFrames(wire, sub.emit);
  assert.equal(stepList(sub.state)[0].val_hard, 0.9);
});

test("a malformed frame is dropped, not thrown", () => {
  const sub = subscribe();
  assert.doesNotThrow(() => sub.emit("gate_done", "{not json"));
  assert.equal(stepList(sub.state).length, 0);
});
