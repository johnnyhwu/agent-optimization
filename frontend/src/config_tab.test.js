import test from "node:test";
import assert from "node:assert/strict";
import { evalSetEdits, initialConfigTab } from "./config_tab.js";

test("Settings opens on General once the grading has been reviewed", () => {
  assert.equal(
    initialConfigTab({ judge_prompt: { reviewed_at: "2026-01-04T10:00:00Z" } }),
    "general",
  );
});

test("a set whose grading nobody has read opens on Judging", () => {
  assert.equal(initialConfigTab({ judge_prompt: { reviewed_at: null } }), "judging");
  // A set with no judge prompt on the payload at all has certainly not had one
  // reviewed — the absent case must not read as "reviewed".
  assert.equal(initialConfigTab({}), "judging");
  assert.equal(initialConfigTab(undefined), "judging");
});

// --- what a Save actually asserts -------------------------------------------

const card = {
  name: "Billing questions",
  description: "the ones support asks",
  metadata: { team: "support" },
  version: 3,
  judge_prompt: { system_prompt: "You grade.", user_prompt: "Q: {q}" },
};
const draftOf = (extra = {}) => ({
  name: card.name,
  description: card.description,
  metadata: { ...card.metadata },
  system: card.judge_prompt.system_prompt,
  user: card.judge_prompt.user_prompt,
  ...extra,
});

test("opening the dialog and pressing Save changes nothing and sends nothing", () => {
  // The PATCH bumps `version` unconditionally, and `version` is what every other
  // loaded copy of this card compares against. Moving it for a non-edit turns
  // the conflict message into something people learn to click past.
  assert.deepEqual(evalSetEdits(card, draftOf()), []);
});

test("the prefilled judge prompt is not mistaken for a written one", () => {
  // The textareas arrive holding the *effective* prompt, which the server
  // resolved from the shipped default. Text in the box is not an override.
  const neverOverridden = { ...card, judge_prompt: { system_prompt: "Default.", user_prompt: "Default." } };
  assert.deepEqual(
    evalSetEdits(neverOverridden, draftOf({ system: "Default.", user: "Default." })),
    [],
  );
});

test("every versioned field is watched, including the metadata rows", () => {
  assert.deepEqual(evalSetEdits(card, draftOf({ name: "Billing" })), ["name"]);
  assert.deepEqual(evalSetEdits(card, draftOf({ description: "" })), ["description"]);
  assert.deepEqual(evalSetEdits(card, draftOf({ system: "You grade harshly." })), ["judge_system_prompt"]);
  assert.deepEqual(evalSetEdits(card, draftOf({ user: "Q: {q}!" })), ["judge_user_prompt"]);

  // A row added, a row removed, and a value rewritten are all edits — the shape
  // is a map, so a length check alone would miss the last of the three.
  assert.deepEqual(evalSetEdits(card, draftOf({ metadata: { team: "support", tier: "1" } })), ["metadata"]);
  assert.deepEqual(evalSetEdits(card, draftOf({ metadata: {} })), ["metadata"]);
  assert.deepEqual(evalSetEdits(card, draftOf({ metadata: { team: "billing" } })), ["metadata"]);
  assert.deepEqual(evalSetEdits(card, draftOf({ metadata: { tier: "1" } })), ["metadata"]);
});

test("a card with nothing filled in is not an edit either", () => {
  const bare = { name: "New set", version: 1 };
  assert.deepEqual(
    evalSetEdits(bare, { name: "New set", description: "", metadata: {}, system: "", user: "" }),
    [],
  );
});
