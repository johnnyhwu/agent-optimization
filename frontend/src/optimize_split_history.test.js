import test from "node:test";
import assert from "node:assert/strict";

import { LIMIT, canUndo, empty, push, reset, undo } from "./optimize_split_history.js";
import { duplicateAll, excludeAll, makeSplit, moveAll } from "./optimize_split.js";

const q = (id) => ({
  item_key: id,
  question_id: id,
  question: `text of ${id}`,
  eval_set_name: "set A",
  skills: ["billing"],
  prior_accuracy: null,
  prior_runs: 0,
});

const split = (train = [], val = [], excluded = []) =>
  makeSplit([...train, ...val, ...excluded].map(q), { train, val, excluded });

test("undoing an empty history is a no-op rather than a crash", () => {
  // The Undo button is disabled in this state, but a keyboard shortcut is not
  // disableable and Ctrl+Z on a fresh step has to do nothing quietly.
  const { history, split: restored } = undo(empty());
  assert.deepEqual(history, []);
  assert.equal(restored, null);
  assert.equal(canUndo(empty()), false);
});

test("undo returns the split as it was before the edit", () => {
  const before = split(["a", "b"], ["c"]);
  const after = moveAll(before, "train", "val");
  const history = push(empty(), before);

  assert.ok(canUndo(history));
  const { split: restored, history: rest } = undo(history);
  assert.equal(restored, before, "the same object, not a copy of it");
  assert.deepEqual(restored.train, ["a", "b"]);
  assert.deepEqual(after.train, []);
  assert.equal(canUndo(rest), false);
});

test("several edits undo in reverse order", () => {
  const s0 = split(["a", "b"], ["c"]);
  const s1 = duplicateAll(s0, "train", "val");
  const s2 = excludeAll(s1, "train");

  let history = push(push(empty(), s0), s1);
  let step = undo(history);
  assert.equal(step.split, s1);
  step = undo(step.history);
  assert.equal(step.split, s0);
  assert.equal(canUndo(step.history), false);
  // The two edits really did change something, so this is not asserting on
  // three identical objects.
  assert.notDeepEqual(s2.train, s0.train);
});

test("the history is capped, and it is the oldest entry that goes", () => {
  // Unbounded, this holds a snapshot per keystroke-equivalent for as long as the
  // wizard is open. The oldest is the one least likely to be wanted back.
  let history = empty();
  for (let i = 0; i < LIMIT + 10; i += 1) history = push(history, split([`k${i}`]));
  assert.equal(history.length, LIMIT);
  assert.deepEqual(history[0].train, ["k10"]);
});

test("reset empties the history", () => {
  // The reason this exists: Wizard.jsx rebuilds the split from scratch when the
  // skill selection changes, and an undo across that boundary would restore the
  // previous skill's questions. `makeSplit` filters keys it does not know, so
  // the result would not be an error — it would be a silently half-empty
  // editor, which is the worst way for this to go wrong.
  const history = push(push(empty(), split(["a"])), split(["b"]));
  assert.equal(history.length, 2);
  assert.deepEqual(reset(), []);
});

test("push does not mutate the history it was given", () => {
  // Same reason every operation in optimize_split.js returns a new object:
  // React re-renders on identity, and an in-place push would leave the Undo
  // button's disabled state describing the previous edit.
  const history = push(empty(), split(["a"]));
  const next = push(history, split(["b"]));
  assert.equal(history.length, 1);
  assert.equal(next.length, 2);
  assert.notEqual(history, next);
});
