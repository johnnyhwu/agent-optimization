import test from "node:test";
import assert from "node:assert/strict";

import { makeSplit } from "./optimize_split.js";
import {
  STEPS,
  blockingReason,
  checkFor,
  extraConfig,
  furthestStep,
  hyperState,
  parseCount,
} from "./optimize_wizard.js";

// Every case below is one where the wizard let a run be started, or a step be
// opened, under a premise that was not true. This screen spends an hour of
// agent calls on whatever it is told, so "it looked fine" is the failure mode
// that matters.

const index = (id) => STEPS.findIndex((s) => s.id === id);

const question = (key, over = {}) => ({
  item_key: key,
  question: `q${key}`,
  eval_set_name: "set",
  prior_accuracy: 0.5,
  ...over,
});

const splitOf = (nTrain, nVal) => {
  const questions = [];
  const train = [];
  const val = [];
  for (let i = 0; i < nTrain; i += 1) {
    questions.push(question(`t${i}`));
    train.push(`t${i}`);
  }
  for (let i = 0; i < nVal; i += 1) {
    questions.push(question(`v${i}`));
    val.push(`v${i}`);
  }
  return makeSplit(questions, { train, val });
};

const okCheck = (skill, over = {}) => ({
  skill,
  status: "ok",
  result: { exists: true, has_frontmatter: true, files: ["SKILL.md"], n_chars: 10, ...over },
});

const ready = (over = {}) => ({
  sourceIds: ["a"],
  preview: { groups: [] },
  skill: "writer",
  split: splitOf(20, 10),
  limits: {},
  check: okCheck("writer"),
  mode: "isolated",
  hyper: {},
  defaults: { num_epochs: 3, batch_size: 4, learning_rate: 2 },
  ...over,
});

// --- The stale check --------------------------------------------------------

test("a check belongs to the skill it was run for, and to no other", () => {
  const check = okCheck("writer");
  assert.equal(checkFor(check, "writer"), check);
  assert.equal(checkFor(check, "router"), null);
  assert.equal(checkFor(check, null), null);
  assert.equal(checkFor(null, "writer"), null);
});

test("changing the skill cannot inherit the previous skill's check", () => {
  // The bug: pick A, check runs, go back, pick B — and the Target step showed
  // A's files while the footer validated B against A's frontmatter flag.
  const state = ready({ skill: "router", check: okCheck("writer") });
  assert.match(blockingReason({ ...state, stepIndex: index("target") }), /Checking the agent/);
});

test("routing mode is blocked by the selected skill's own frontmatter", () => {
  const blocked = ready({
    mode: "routing",
    check: okCheck("writer", {
      has_frontmatter: false,
      routing_blocked_reason: "SKILL.md has no frontmatter block.",
    }),
  });
  assert.equal(
    blockingReason({ ...blocked, stepIndex: index("target") }),
    "SKILL.md has no frontmatter block.",
  );
  // The same run in isolated mode is fine — the modes freeze opposite halves.
  assert.equal(blockingReason({ ...blocked, mode: "isolated", stepIndex: index("target") }), null);
});

// --- The check state machine ------------------------------------------------

test("a failed check does not masquerade as one still running", () => {
  const failed = ready({ check: { skill: "writer", status: "failed", error: "connection refused" } });
  const reason = blockingReason({ ...failed, stepIndex: index("target") });
  assert.match(reason, /could not be checked/);
  assert.match(reason, /connection refused/);
  assert.ok(!reason.includes("Checking"), reason);
});

test("a check that is genuinely in flight says so", () => {
  const checking = ready({ check: { skill: "writer", status: "checking" } });
  assert.match(blockingReason({ ...checking, stepIndex: index("target") }), /Checking the agent/);
});

test("a skill missing from the agent is named, not merely rejected", () => {
  const missing = ready({ check: { skill: "writer", status: "ok", result: { exists: false } } });
  assert.match(blockingReason({ ...missing, stepIndex: index("target") }), /writer/);
});

// --- Reachability -----------------------------------------------------------

test("reachability follows the prerequisite chain, one step at a time", () => {
  assert.equal(furthestStep({ sourceIds: [], hyper: {}, defaults: {} }), index("source"));
  assert.equal(furthestStep({ sourceIds: ["a"], hyper: {}, defaults: {} }), index("source"));

  const loaded = { sourceIds: ["a"], preview: { groups: [] }, hyper: {}, defaults: {} };
  assert.equal(furthestStep(loaded), index("skill"));

  const picked = { ...loaded, skill: "writer", split: splitOf(20, 10), limits: {} };
  assert.equal(furthestStep(picked), index("target"));

  assert.equal(furthestStep(ready()), index("review"));
});

test("a check for the wrong skill does not unlock the rest of the wizard", () => {
  // This is what returned 5 the moment any check existed.
  const state = ready({ skill: "router", check: okCheck("writer") });
  assert.equal(furthestStep(state), index("target"));
});

test("clearing the skill walks reachability back rather than leaving a blank step", () => {
  // Reload the preview and the skill and split go with it. The old wizard kept
  // `check`, so every step stayed reachable and the split step rendered an
  // empty body under "Pick a skill first."
  const state = ready({ skill: null, split: null });
  assert.equal(furthestStep(state), index("skill"));
});

test("an unstartable split stops the wizard at the split step", () => {
  const tiny = ready({ split: splitOf(1, 0), limits: { min_train: 4, min_val: 2 } });
  assert.equal(furthestStep(tiny), index("split"));
  assert.ok(blockingReason({ ...tiny, stepIndex: index("split") }));
});

test("the step bar and the Continue button share one definition of ready", () => {
  // furthestStep is defined as "every earlier step is unblocked", so there is
  // no state where the bar offers a step the button would refuse to reach.
  const state = ready();
  for (let i = 0; i <= furthestStep(state); i += 1) {
    if (i === furthestStep(state)) break;
    assert.equal(blockingReason({ ...state, stepIndex: i }), null, `step ${i} should be clear`);
  }
});

// --- Numbers ----------------------------------------------------------------

test("an emptied number field is an error, not a zero", () => {
  // `Number("")` is 0, which the controlled input rendered straight back, so
  // the field could not be cleared and 0 reached createOptimizationRun.
  assert.deepEqual(parseCount("", { min: 1 }), { value: null, error: "Required." });
});

test("a number field rejects what is not a whole count", () => {
  assert.match(parseCount("2.5", { min: 1 }).error, /Whole numbers/);
  assert.match(parseCount("abc", { min: 1 }).error, /Whole numbers/);
  assert.match(parseCount("-3", { min: 1 }).error, /Whole numbers/);
});

test("a number field enforces its own bounds", () => {
  assert.match(parseCount("0", { min: 1 }).error, /at least 1/);
  assert.match(parseCount("99", { min: 1, max: 20 }).error, /at most 20/);
  assert.deepEqual(parseCount("7", { min: 1, max: 20 }), { value: 7, error: null });
});

test("untouched fields take the server's defaults and are never errors", () => {
  const state = hyperState({}, { num_epochs: 3, batch_size: 4, learning_rate: 2 });
  assert.equal(state.ok, true);
  assert.equal(state.values.num_epochs, 3);
  assert.equal(state.values.batch_size, 4);
});

test("one bad field is reported without discarding the good ones", () => {
  const state = hyperState({ num_epochs: "", batch_size: "8" }, { learning_rate: 2 });
  assert.equal(state.ok, false);
  assert.ok(state.errors.num_epochs);
  assert.equal(state.values.batch_size, 8);
  assert.equal(state.values.learning_rate, 2);
});

test("the run cannot be started while a training number is invalid", () => {
  // The last gate. `start()` used to send whatever `Number(raw)` produced.
  const bad = ready({ hyper: { num_epochs: "0" } });
  assert.ok(blockingReason({ ...bad, stepIndex: index("review") }));
  assert.equal(furthestStep(bad), index("review"));

  const good = ready({ hyper: { num_epochs: "4" } });
  assert.equal(blockingReason({ ...good, stepIndex: index("review") }), null);
});

test("the three named hyperparameters are not duplicated into config", () => {
  const extra = extraConfig({ num_epochs: "3", batch_size: "4", learning_rate: "2", seed: "42" });
  assert.deepEqual(extra, { seed: "42" });
});
