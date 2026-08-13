import test from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_SORT,
  actionsFor,
  counts,
  duplicate,
  exclude,
  makeSplit,
  move,
  restore,
  sortQuestions,
  splitIssues,
} from "./optimize_split.js";

const q = (id, extra = {}) => ({
  item_key: id,
  question_id: id,
  question: `text of ${id}`,
  eval_set_name: "set A",
  skills: ["billing"],
  prior_accuracy: null,
  prior_runs: 0,
  ...extra,
});

function split(train = [], val = [], excluded = []) {
  return makeSplit(
    [...train, ...val, ...excluded].map((k) => q(k)),
    { train, val, excluded },
  );
}

// --- Moving questions between the two columns -------------------------------

test("moving a question takes it out of the column it came from", () => {
  // A move that only added would leave the question in both columns — which is
  // the overlap case, silently, without the warning that makes overlap a
  // decision rather than an accident.
  const after = move(split(["a", "b"], ["c"]), "a", "val");
  assert.deepEqual(after.train, ["b"]);
  assert.deepEqual(after.val, ["c", "a"]);
});

test("duplicating leaves the question in both columns", () => {
  // The feature exists for small sets, where a developer may reasonably want a
  // question counted twice. It has to be distinguishable from a move, because
  // the overlap warning is what tells them the gate is weakened by it.
  const after = duplicate(split(["a", "b"], ["c"]), "a", "val");
  assert.deepEqual(after.train, ["a", "b"]);
  assert.deepEqual(after.val, ["c", "a"]);
});

test("duplicating a question that is already in the other column changes nothing", () => {
  // Otherwise the same key lands in `val` twice, the count on screen says one
  // more question than the run will have, and the server de-duplicates it back
  // without telling anyone.
  const before = split(["a"], ["a", "b"]);
  const after = duplicate(before, "a", "val");
  assert.deepEqual(after.val, ["a", "b"]);
});

test("excluding removes a question from both columns and remembers it", () => {
  // Exclude means "not in this run", not "delete from the eval set" — which is
  // why the button is an ✕ and not a bin, and why the question has to come back.
  const after = exclude(split(["a", "b"], ["a", "c"]), "a");
  assert.deepEqual(after.train, ["b"]);
  assert.deepEqual(after.val, ["c"]);
  assert.deepEqual(after.excluded, ["a"]);
});

test("restoring puts a question back into the training column", () => {
  const after = restore(split(["b"], ["c"], ["a"]), "a");
  assert.ok(after.train.includes("a"));
  assert.deepEqual(after.excluded, []);
});

test("every operation returns a new object rather than mutating", () => {
  // React re-renders on identity. An in-place mutation would update the data
  // and leave the screen showing the previous split — the worst possible
  // failure here, because the developer would then start the run they can see.
  const before = split(["a", "b"], ["c"]);
  const after = move(before, "a", "val");
  assert.deepEqual(before.train, ["a", "b"]);
  assert.notEqual(before, after);
});

test("an unknown key is a no-op rather than an error", () => {
  const before = split(["a"], ["b"]);
  assert.deepEqual(move(before, "zzz", "val"), before);
});

// --- What the buttons offer -------------------------------------------------

test("a question already in the other column cannot be moved or duplicated there", () => {
  // Both would be no-ops. A button that looks available and does nothing is
  // read as a broken tool, so the row says `in both` instead.
  const actions = actionsFor(split(["a"], ["a", "b"]), "a", "train");
  assert.equal(actions.move.enabled, false);
  assert.equal(actions.duplicate.enabled, false);
  assert.ok(actions.inBoth);
});

test("a disabled action always comes with a reason", () => {
  // A disabled control with no explanation is a puzzle. Every one of these
  // carries the sentence the tooltip shows.
  const actions = actionsFor(split(["a"], ["a"]), "a", "train");
  assert.ok(actions.move.reason);
  assert.ok(actions.duplicate.reason);
});

test("an ordinary question can be moved, duplicated and excluded", () => {
  const actions = actionsFor(split(["a", "b"], ["c"]), "a", "train");
  assert.equal(actions.move.enabled, true);
  assert.equal(actions.duplicate.enabled, true);
  assert.equal(actions.exclude.enabled, true);
  assert.ok(!actions.inBoth);
});

test("the last question in a column can still be moved out", () => {
  // The size gate refuses to start such a run anyway. Blocking the move as well
  // would trap someone who is mid-rearrangement, with no way to get from one
  // valid split to another.
  const actions = actionsFor(split(["a"], ["b"]), "a", "train");
  assert.equal(actions.move.enabled, true);
});

// --- Sorting ----------------------------------------------------------------

test("sorting by accuracy puts the questions the agent fails first", () => {
  // The whole reason to sort: those are the ones a skill edit might fix, and
  // they are what a developer wants in the training split.
  const questions = [
    q("easy", { prior_accuracy: 1.0, prior_runs: 3 }),
    q("hard", { prior_accuracy: 0.0, prior_runs: 3 }),
    q("mid", { prior_accuracy: 0.5, prior_runs: 3 }),
  ];
  assert.deepEqual(
    sortQuestions(questions, "accuracy").map((x) => x.question_id),
    ["hard", "mid", "easy"],
  );
});

test("never-run questions sort after the ones with data, not as zero", () => {
  // Treated as 0% they would top an accuracy sort — presented as the worst
  // questions in the set when nothing at all is known about them.
  const questions = [
    q("known", { prior_accuracy: 0.2, prior_runs: 4 }),
    q("new"),
  ];
  assert.deepEqual(
    sortQuestions(questions, "accuracy").map((x) => x.question_id),
    ["known", "new"],
  );
});

test("sorting never loses or duplicates a question", () => {
  const questions = ["a", "b", "c", "d"].map((id) => q(id));
  for (const mode of [DEFAULT_SORT, "accuracy", "eval_set"]) {
    const sorted = sortQuestions(questions, mode);
    assert.equal(sorted.length, questions.length);
    assert.deepEqual(
      sorted.map((x) => x.item_key).sort(),
      questions.map((x) => x.item_key).sort(),
    );
  }
});

test("sorting is stable for questions that compare equal", () => {
  // Otherwise the list reshuffles under the cursor every time the component
  // re-renders, and the row someone was about to click moves.
  const questions = ["b", "a", "c"].map((id) => q(id, { prior_accuracy: 0.5, prior_runs: 1 }));
  assert.deepEqual(
    sortQuestions(questions, "accuracy").map((x) => x.question_id),
    ["b", "a", "c"],
  );
});

// --- Counts and warnings ----------------------------------------------------

test("the counts report each column and the overlap separately", () => {
  const c = counts(split(["a", "b", "c"], ["c", "d"], ["e"]));
  assert.equal(c.train, 3);
  assert.equal(c.val, 2);
  assert.equal(c.overlap, 1);
  assert.equal(c.excluded, 1);
});

test("a split below the minimum reports an error that blocks Start", () => {
  // The server refuses it too. The point of checking here is that the developer
  // finds out while they can still fix it, rather than on a 400 after filling in
  // three more screens.
  const issues = splitIssues(split(["a", "b"], ["c"]), { min_train: 8, min_val: 5 });
  const codes = issues.filter((i) => i.level === "error").map((i) => i.code);
  assert.ok(codes.includes("train_too_small"));
  assert.ok(codes.includes("val_too_small"));
});

test("a workable split warns without blocking", () => {
  const train = Array.from({ length: 10 }, (_, i) => `t${i}`);
  const val = Array.from({ length: 6 }, (_, i) => `v${i}`);
  const issues = splitIssues(split(train, val), {
    min_train: 8, min_val: 5, warn_train: 20, warn_val: 10,
  });
  assert.equal(issues.filter((i) => i.level === "error").length, 0);
  assert.ok(issues.some((i) => i.level === "warning"));
});

test("overlap is a warning and names the questions", () => {
  const train = Array.from({ length: 20 }, (_, i) => `t${i}`);
  const val = [...Array.from({ length: 10 }, (_, i) => `v${i}`), "t0"];
  const issues = splitIssues(split(train, val), {
    min_train: 8, min_val: 5, warn_train: 20, warn_val: 10,
  });
  const overlap = issues.find((i) => i.code === "overlap");
  assert.equal(overlap.level, "warning");
  assert.deepEqual(overlap.item_keys, ["t0"]);
});

test("a comfortable split produces no issues at all", () => {
  const train = Array.from({ length: 40 }, (_, i) => `t${i}`);
  const val = Array.from({ length: 20 }, (_, i) => `v${i}`);
  assert.deepEqual(
    splitIssues(split(train, val), {
      min_train: 8, min_val: 5, warn_train: 20, warn_val: 10,
    }),
    [],
  );
});

test("the browser's thresholds come from the server, not from a copy", () => {
  // Same numbers, one source. A hardcoded 8 here would drift from the server's
  // and enable Start on a request that 400s.
  const train = Array.from({ length: 9 }, (_, i) => `t${i}`);
  const val = Array.from({ length: 6 }, (_, i) => `v${i}`);
  const strict = splitIssues(split(train, val), { min_train: 12, min_val: 5 });
  assert.ok(strict.some((i) => i.code === "train_too_small"));
});
