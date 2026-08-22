import test from "node:test";
import assert from "node:assert/strict";

import {
  fromStored,
  overrides,
  parse,
  placeholder,
  errorsOf,
  SYSTEM,
  OFF,
  SET,
} from "./settings_fields.js";

// The settings page's one rule, in a module `node --test` can load.
//
// A field is blank until you type in it, and blank means "I have no opinion —
// use whatever the deployment says". That is the whole state machine: the
// presence of text *is* the override, so there is nothing to badge and no reset
// button to add. Clearing the box is the reset.
//
// Two kinds of field cannot say that with a blank, and they are the ones worth
// testing hardest:
//
//   * a checkbox has no empty state, so `diagnosis_enabled`, `slow_update` and
//     `meta_skill` get three positions — follow the system, force on, force off
//   * `early_stop_target_score` already uses blank to mean "aim at nothing", so
//     it needs its own third position rather than overloading the first
//
// And the numbers that reach the wizard as percents are stored as fractions.
// The wizard owns that conversion (`HYPER_FIELDS[...].scale`); this module reads
// it from there rather than knowing it too, because two copies of "is 0.25 a
// quarter or a quarter of a percent" is exactly the sort of thing that ships.

const TEXT = { key: "judge_model", kind: "text", minimum: null, maximum: null };
const NUMBER = {
  key: "concurrency", kind: "int", minimum: 1, maximum: 32,
};
const FLOAT = {
  key: "agent_timeout_s", kind: "float", minimum: null, maximum: null,
};
const BOOL = { key: "diagnosis_enabled", kind: "bool" };
const SHARE = {
  key: "early_stop_train_error_share", kind: "fraction", minimum: 0, maximum: 1,
};
const TARGET = {
  key: "early_stop_target_score", kind: "fraction", minimum: 0, maximum: 1,
  optional: true,
};

const CATALOG = [TEXT, NUMBER, FLOAT, BOOL, SHARE, TARGET];

// --- Blank means "no opinion" ----------------------------------------------

test("an untouched form overrides nothing", () => {
  const form = fromStored(CATALOG, {});
  assert.deepEqual(overrides(CATALOG, form), {});
});

test("typing a value makes it an override", () => {
  const form = { ...fromStored(CATALOG, {}), judge_model: { mode: SET, raw: "Qwen3-72B" } };
  assert.deepEqual(overrides(CATALOG, form), { judge_model: "Qwen3-72B" });
});

test("clearing the box removes the override", () => {
  const form = fromStored(CATALOG, { judge_model: "Qwen3-72B" });
  assert.deepEqual(overrides(CATALOG, form), { judge_model: "Qwen3-72B" });
  const cleared = { ...form, judge_model: { mode: SYSTEM, raw: "" } };
  assert.deepEqual(overrides(CATALOG, cleared), {});
});

test("whitespace is not an override", () => {
  const form = { ...fromStored(CATALOG, {}), judge_model: { mode: SET, raw: "   " } };
  assert.deepEqual(overrides(CATALOG, form), {});
});

test("a stored value comes back in the box", () => {
  const form = fromStored(CATALOG, { concurrency: 4 });
  assert.equal(form.concurrency.mode, SET);
  assert.equal(form.concurrency.raw, "4");
});

// --- The three-position controls -------------------------------------------

test("a boolean following the system overrides nothing", () => {
  const form = fromStored(CATALOG, {});
  assert.equal(form.diagnosis_enabled.mode, SYSTEM);
  assert.deepEqual(overrides(CATALOG, form), {});
});

test("a boolean forced off stores false, not nothing", () => {
  // The whole reason this control has three positions rather than two: `false`
  // is an answer, and a checkbox cannot tell it apart from "not set".
  const form = { ...fromStored(CATALOG, {}), diagnosis_enabled: { mode: SET, raw: "false" } };
  assert.deepEqual(overrides(CATALOG, form), { diagnosis_enabled: false });
});

test("a boolean forced on stores true", () => {
  const form = { ...fromStored(CATALOG, {}), diagnosis_enabled: { mode: SET, raw: "true" } };
  assert.deepEqual(overrides(CATALOG, form), { diagnosis_enabled: true });
});

test("a stored false comes back as forced off, not as unset", () => {
  const form = fromStored(CATALOG, { diagnosis_enabled: false });
  assert.equal(form.diagnosis_enabled.mode, SET);
  assert.equal(form.diagnosis_enabled.raw, "false");
});

test("target score can be overridden to off", () => {
  const form = { ...fromStored(CATALOG, {}), early_stop_target_score: { mode: OFF, raw: "" } };
  assert.deepEqual(overrides(CATALOG, form), { early_stop_target_score: null });
});

test("a stored null target score comes back as off rather than as unset", () => {
  const form = fromStored(CATALOG, { early_stop_target_score: null });
  assert.equal(form.early_stop_target_score.mode, OFF);
});

test("only an optional field may be switched off", () => {
  const form = { ...fromStored(CATALOG, {}), concurrency: { mode: OFF, raw: "" } };
  assert.throws(() => overrides(CATALOG, form), /concurrency/);
});

// --- Percent scaling --------------------------------------------------------

test("a share is typed as a percent and stored as a fraction", () => {
  const form = {
    ...fromStored(CATALOG, {}),
    early_stop_train_error_share: { mode: SET, raw: "25" },
  };
  assert.deepEqual(overrides(CATALOG, form), { early_stop_train_error_share: 0.25 });
});

test("a stored fraction comes back as a percent", () => {
  const form = fromStored(CATALOG, { early_stop_train_error_share: 0.25 });
  assert.equal(form.early_stop_train_error_share.raw, "25");
});

test("a zero share survives the round trip", () => {
  const form = fromStored(CATALOG, { early_stop_train_error_share: 0 });
  assert.equal(form.early_stop_train_error_share.mode, SET);
  assert.deepEqual(overrides(CATALOG, form), { early_stop_train_error_share: 0 });
});

// --- Parsing and validation -------------------------------------------------

test("a non-numeric entry is an error, not a zero", () => {
  assert.equal(parse(NUMBER, "abc").ok, false);
});

test("a value above the ceiling is an error", () => {
  assert.equal(parse(NUMBER, "64").ok, false);
  assert.equal(parse(NUMBER, "32").ok, true);
});

test("a value below the floor is an error", () => {
  assert.equal(parse(NUMBER, "0").ok, false);
});

test("an integer field refuses a fraction", () => {
  assert.equal(parse(NUMBER, "2.5").ok, false);
  assert.equal(parse(FLOAT, "2.5").ok, true);
});

test("errorsOf reports per key and says nothing about untouched fields", () => {
  const form = { ...fromStored(CATALOG, {}), concurrency: { mode: SET, raw: "64" } };
  const errors = errorsOf(CATALOG, form);
  assert.ok(errors.concurrency);
  assert.equal(errors.judge_model, undefined);
});

test("a field with an error is not silently dropped from the overrides", () => {
  // Saving a form with an error must be refused, not partly applied.
  const form = { ...fromStored(CATALOG, {}), concurrency: { mode: SET, raw: "64" } };
  assert.throws(() => overrides(CATALOG, form), /concurrency/);
});

// --- What the empty box says ------------------------------------------------

test("the placeholder is the system value", () => {
  assert.equal(placeholder(TEXT, "Qwen3.6-27B"), "Qwen3.6-27B");
  assert.equal(placeholder(NUMBER, 1), "1");
});

test("a fraction is shown as a percent in the placeholder", () => {
  assert.equal(placeholder(SHARE, 0.25), "25");
});

test("an unset system value reads as blank rather than as null", () => {
  assert.equal(placeholder(TEXT, ""), "");
  assert.equal(placeholder(TARGET, null), "");
});
