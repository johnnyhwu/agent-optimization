import test from "node:test";
import assert from "node:assert/strict";

import { errorStreak, stopConditions, stopSentence } from "./optimize_stopping.js";

// A run is an hour of paid agent calls, and until now the only thing the page
// could say about its ending was a step counter. These are the two questions a
// reader has instead: what can end this, and — afterwards — what did.
//
// The trap in the first is showing conditions that are switched off. A row
// listing "no new best: 0 — never" reads as a live condition, and the reader
// then waits for something that is never coming.

const run = (config = {}, extra = {}) => ({
  total_steps: 4,
  best_step: 0,
  best_score: 0.5,
  config,
  ...extra,
});

const step = (step_no, extra = {}) => ({ step_no, status: "done", ...extra });

test("running out of steps is always one of the endings", () => {
  const ids = stopConditions(run(), [step(0), step(1)]).map((c) => c.id);

  assert.deepEqual(ids, ["steps"]);
});

test("a condition that is switched off is not listed at all", () => {
  const conditions = stopConditions(
    run({ early_stop_patience: 0, early_stop_val_error_streak: 0 }),
    [step(0)],
  );

  assert.deepEqual(conditions.map((c) => c.id), ["steps"]);
});

test("patience shows how many steps of it are left", () => {
  const conditions = stopConditions(
    run({ early_stop_patience: 5 }, { best_step: 2 }),
    [step(0), step(1), step(2), step(3), step(4)],
  );

  const patience = conditions.find((c) => c.id === "patience");
  assert.equal(patience.progress, "2/5 steps");
  assert.equal(patience.met, false);
});

test("patience reports itself met on the step that trips it", () => {
  const conditions = stopConditions(
    run({ early_stop_patience: 2 }, { best_step: 1 }),
    [step(0), step(1), step(2), step(3)],
  );

  assert.equal(conditions.find((c) => c.id === "patience").met, true);
});

test("a target of zero is off, not a condition every run has met", () => {
  const ids = stopConditions(run({ early_stop_target_score: 0 }), [step(0)]).map((c) => c.id);

  assert.deepEqual(ids, ["steps"]);
});

test("the target is stated against the best score so far", () => {
  const conditions = stopConditions(
    run({ early_stop_target_score: 0.9 }, { best_score: 0.82 }),
    [step(0)],
  );

  assert.equal(conditions.find((c) => c.id === "target").progress, "82% of 90%");
});

test("an unanswered-questions condition carries both halves of its pair", () => {
  const conditions = stopConditions(
    run({ early_stop_val_error_streak: 3, early_stop_val_error_share: 0.25 }),
    [step(0), step(1, { gate_reject_reason: "val_errors" })],
  );

  const errors = conditions.find((c) => c.id === "val-errors");
  assert.equal(errors.progress, "1/3 steps over 25%");
});

// --- The streak, which has to agree with the server's copy -------------------

test("a clean step clears the streak", () => {
  const steps = [
    step(1, { gate_reject_reason: "val_errors" }),
    step(2, { gate_action: "reject", gate_reject_reason: "accuracy" }),
  ];

  assert.equal(errorStreak(steps, "val"), 0);
});

test("a step that never reached validation does not clear the validation streak", () => {
  const steps = [
    step(1, { gate_reject_reason: "val_errors" }),
    step(2, { gate_reject_reason: "train_errors" }),
    step(3, { gate_reject_reason: "val_errors" }),
  ];

  assert.equal(errorStreak(steps, "val"), 2);
});

test("a step still running is not evidence either way", () => {
  const steps = [
    step(1, { gate_reject_reason: "val_errors" }),
    { step_no: 2, status: "running" },
  ];

  assert.equal(errorStreak(steps, "val"), 1);
});

// --- What ended it -----------------------------------------------------------

test("an ordinary finish needs no sentence", () => {
  assert.equal(stopSentence({ stop_reason: "finished", config: {} }, []), null);
});

test("a run from before stop_reason existed says nothing rather than guessing", () => {
  assert.equal(stopSentence({ config: {} }, []), null);
});

test("running out of patience says so, with the step it happened on", () => {
  const sentence = stopSentence(
    { stop_reason: "early_stop_patience", config: { early_stop_patience: 3 } },
    [step(0), step(6)],
  );

  assert.match(sentence, /step 6/);
  assert.match(sentence, /3 steps/);
});

test("reaching the target reads as the success it is", () => {
  const sentence = stopSentence(
    { stop_reason: "early_stop_target", config: { early_stop_target_score: 0.9 } },
    [step(3)],
  );

  assert.match(sentence, /reached the target of 90%/);
});

test("an outage says it was the agent server, not the skill", () => {
  const sentence = stopSentence(
    {
      stop_reason: "early_stop_val_errors",
      config: { early_stop_val_error_streak: 2, early_stop_val_error_share: 0.25 },
    },
    [step(5)],
  );

  assert.match(sentence, /2 validation rollouts/);
  assert.match(sentence, /25%/);
  assert.match(sentence, /agent server, not the skill/);
});

test("a reason this build has never heard of is still printed", () => {
  const sentence = stopSentence({ stop_reason: "early_stop_budget", config: {} }, []);

  assert.match(sentence, /early stop budget/);
});
