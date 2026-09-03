import test from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_SORT,
  actionsFor,
  canStart,
  counts,
  duplicate,
  duplicateAll,
  exclude,
  excludeAll,
  makeSplit,
  move,
  moveAll,
  restore,
  sortQuestions,
  splitIssues,
} from "./optimize_split.js";

// What `/optimization/defaults` sends, spelled out once. Passing it explicitly
// rather than leaning on the defaults is the point of the last test in this
// file: these thresholds belong to the server, and a copy here would drift from
// the one the create endpoint enforces.
const LIMITS = {
  min_train: 1, min_val: 1,
  soft_train: 8, soft_val: 5,
  warn_train: 20, warn_val: 10,
};

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

test("excluding without naming a column takes the question out of the run", () => {
  // Exclude means "not in this run", not "delete from the eval set" — which is
  // why the button is an ✕ and not a bin, and why the question has to come back.
  const after = exclude(split(["a", "b"], ["a", "c"]), "a");
  assert.deepEqual(after.train, ["b"]);
  assert.deepEqual(after.val, ["c"]);
  assert.deepEqual(after.excluded, ["a"]);
});

test("excluding from one column leaves the copy in the other alone", () => {
  // The ✕ used to clear both columns whichever row it was pressed on, so a
  // question that had just been copied to validation vanished from there too,
  // with nothing on screen to say so. The button is on a row; it acts on that
  // row.
  const after = exclude(split(["a", "b"], ["a", "c"]), "a", "train");
  assert.deepEqual(after.train, ["b"]);
  assert.deepEqual(after.val, ["a", "c"], "the validation copy is still there");
  assert.deepEqual(after.excluded, [], "and the run has not lost the question");
});

test("excluding the last copy is what puts a question in the drawer", () => {
  const once = exclude(split(["a"], ["a"]), "a", "train");
  assert.deepEqual(once.excluded, []);
  const twice = exclude(once, "a", "val");
  assert.deepEqual(twice.excluded, ["a"]);
  assert.deepEqual(twice.train, []);
  assert.deepEqual(twice.val, []);
});

test("excluding a question the drawer already holds does not list it twice", () => {
  const after = exclude(split(["b"], ["c"], ["a"]), "a");
  assert.deepEqual(after.excluded, ["a"]);
});

test("restoring puts a question back into the training column by default", () => {
  const after = restore(split(["b"], ["c"], ["a"]), "a");
  assert.ok(after.train.includes("a"));
  assert.deepEqual(after.excluded, []);
});

test("restoring can name the column instead", () => {
  // The drawer's rows offer both, so that putting a question back into
  // validation is one click rather than restore-then-move.
  const after = restore(split(["b"], ["c"], ["a"]), "a", "val");
  assert.deepEqual(after.val, ["c", "a"]);
  assert.ok(!after.train.includes("a"));
  assert.deepEqual(after.excluded, []);
});

// --- The same three things, to a whole column -------------------------------

test("moving a whole column empties it into the other one", () => {
  // The case that motivated this: sixty questions is sixty clicks.
  const after = moveAll(split(["a", "b", "c"], ["d"]), "train", "val");
  assert.deepEqual(after.train, []);
  assert.deepEqual(after.val, ["d", "a", "b", "c"]);
});

test("copying a whole column leaves it where it is", () => {
  const after = duplicateAll(split(["a", "b"], ["c"]), "train", "val");
  assert.deepEqual(after.train, ["a", "b"]);
  assert.deepEqual(after.val, ["c", "a", "b"]);
  // Which is the overlap case, and the editor says so — deliberately, because
  // it is what the developer asked for.
  assert.equal(counts(after).overlap, 2);
});

test("a bulk copy does not duplicate what is already in the target", () => {
  const after = duplicateAll(split(["a", "b"], ["a"]), "train", "val");
  assert.deepEqual(after.val, ["a", "b"]);
});

test("excluding a whole column leaves questions that are also in the other one", () => {
  // `excludeAll` is a fold over the per-row exclude, so it inherits the rule
  // that a question with a copy elsewhere has not left the run. Anything else
  // would make "clear this column" quietly destructive.
  const after = excludeAll(split(["a", "b"], ["a"]), "train");
  assert.deepEqual(after.train, []);
  assert.deepEqual(after.val, ["a"]);
  assert.deepEqual(after.excluded, ["b"]);
});

test("a bulk operation reads the column it is about to change", () => {
  // `moveAll` empties the list it is iterating. Folding over the live split
  // rather than a snapshot of the keys skips every other question.
  const before = split(["a", "b", "c", "d", "e"], []);
  const after = moveAll(before, "train", "val");
  assert.equal(after.val.length, 5);
  assert.deepEqual(before.train, ["a", "b", "c", "d", "e"], "and does not mutate");
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

test("an empty column reports an error that blocks Start", () => {
  // The server refuses it too. The point of checking here is that the developer
  // finds out while they can still fix it, rather than on a 400 after filling in
  // three more screens.
  //
  // Empty is the *only* size that blocks. It is not a judgement about how good
  // the run would be: with no training questions a minibatch does not exist,
  // and with no validation questions the gate has nothing to compare.
  const issues = splitIssues(split([], []), LIMITS);
  const codes = issues.filter((i) => i.level === "error").map((i) => i.code);
  assert.ok(codes.includes("train_empty"));
  assert.ok(codes.includes("val_empty"));
});

test("a tiny split warns but still starts", () => {
  // The case the old floor of 8/5 refused, and the reason it was lowered:
  // three questions is a bad experiment and a perfectly good check that the
  // pipeline works before an hour of agent calls is spent on sixty.
  const issues = splitIssues(split(["a", "b", "c"], ["d", "e"]), LIMITS);
  assert.equal(issues.filter((i) => i.level === "error").length, 0);
  assert.deepEqual(
    issues.map((i) => i.code).sort(),
    ["train_too_small", "val_too_small"],
  );
  assert.ok(canStart(split(["a", "b", "c"], ["d", "e"]), LIMITS));
});

test("each column raises exactly one size issue", () => {
  // Three tiers, and a column belongs to one of them. A split of six training
  // questions is very small *and* thin, and saying so twice would put two boxes
  // on screen describing one number.
  for (const [n, expected] of [[0, "train_empty"], [3, "train_too_small"],
                               [12, "train_small"], [30, null]]) {
    const train = Array.from({ length: n }, (_, i) => `t${i}`);
    const val = Array.from({ length: 10 }, (_, i) => `v${i}`);
    const codes = splitIssues(split(train, val), LIMITS)
      .map((i) => i.code)
      .filter((c) => c.startsWith("train"));
    assert.deepEqual(codes, expected ? [expected] : [], `train of ${n}`);
  }
});

test("a workable split warns without blocking", () => {
  const train = Array.from({ length: 10 }, (_, i) => `t${i}`);
  const val = Array.from({ length: 6 }, (_, i) => `v${i}`);
  const issues = splitIssues(split(train, val), LIMITS);
  assert.equal(issues.filter((i) => i.level === "error").length, 0);
  assert.ok(issues.some((i) => i.level === "warning"));
});

test("overlap is a warning and names the questions", () => {
  const train = Array.from({ length: 20 }, (_, i) => `t${i}`);
  const val = [...Array.from({ length: 10 }, (_, i) => `v${i}`), "t0"];
  const issues = splitIssues(split(train, val), LIMITS);
  const overlap = issues.find((i) => i.code === "overlap");
  assert.equal(overlap.level, "warning");
  assert.deepEqual(overlap.item_keys, ["t0"]);
});

test("a comfortable split produces no issues at all", () => {
  const train = Array.from({ length: 40 }, (_, i) => `t${i}`);
  const val = Array.from({ length: 20 }, (_, i) => `v${i}`);
  assert.deepEqual(
    splitIssues(split(train, val), LIMITS),
    [],
  );
});

test("every issue carries something to do about it", () => {
  // The whole point of the rewrite. A warning that only describes the split
  // leaves the developer holding an accurate sentence and no next move, which
  // is how three of these were read as decoration.
  const cases = [
    splitIssues(split([], []), LIMITS),
    splitIssues(split(["a"], ["b"]), LIMITS),
    splitIssues(
      split(
        Array.from({ length: 10 }, (_, i) => `t${i}`),
        Array.from({ length: 6 }, (_, i) => `v${i}`),
      ),
      LIMITS,
    ),
    splitIssues(
      split(
        Array.from({ length: 20 }, (_, i) => `t${i}`),
        [...Array.from({ length: 10 }, (_, i) => `v${i}`), "t0"],
      ),
      LIMITS,
    ),
  ];
  const all = cases.flat();
  assert.ok(all.length >= 5, "expected every branch to be covered");
  for (const i of all) {
    for (const field of ["title", "summary", "detail", "suggestion"]) {
      assert.ok(i[field] && i[field].length > 10, `${i.code} has no ${field}`);
    }
    // The one-line form stays available for a caller with one line to spend.
    assert.equal(i.message, `${i.title} — ${i.summary}`);
  }
});

test("an issue's suggestion names the number of questions to move", () => {
  // "Add more questions" is the version that was already there. The arithmetic
  // is the part the developer would otherwise do at the top of the screen.
  const [tooSmall] = splitIssues(split(["a", "b"], ["c", "d", "e", "f", "g"]), LIMITS);
  assert.equal(tooSmall.code, "train_too_small");
  assert.match(tooSmall.suggestion, /6 more question/);
});

test("the browser's thresholds come from the server, not from a copy", () => {
  // Same numbers, one source. A hardcoded 8 here would drift from the server's
  // and enable Start on a request that 400s.
  const train = Array.from({ length: 9 }, (_, i) => `t${i}`);
  const val = Array.from({ length: 6 }, (_, i) => `v${i}`);
  const strict = splitIssues(split(train, val), { ...LIMITS, soft_train: 12 });
  assert.ok(strict.some((i) => i.code === "train_too_small"));
});
