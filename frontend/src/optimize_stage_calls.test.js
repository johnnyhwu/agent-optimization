import { test } from "node:test";
import assert from "node:assert/strict";

import {
  callLabel,
  editCount,
  groupStageCalls,
  summarise,
} from "./optimize_stage_calls.js";

const call = (seq, stage, extra = {}) => ({ seq, stage, ...extra });

test("phases come back in the order the pipeline runs them", () => {
  // Deliberately out of order on the wire: the page must not depend on the
  // order rows happen to arrive in.
  const groups = groupStageCalls([
    call(3, "ranking"),
    call(0, "merge_failure"),
    call(2, "merge_final"),
    call(1, "merge_success"),
  ]);

  assert.deepEqual(
    groups.map((g) => g.key),
    ["merge_failure", "merge_success", "merge_final", "ranking"],
  );
});

test("a phase that did not run is absent, not empty", () => {
  // Ranking is skipped whenever the merged pool already fits. An empty section
  // would claim it ran and did nothing.
  const groups = groupStageCalls([call(0, "merge_failure"), call(1, "merge_final")]);

  assert.deepEqual(groups.map((g) => g.key), ["merge_failure", "merge_final"]);
});

test("several rounds of one merge stay in one phase, in round order", () => {
  const groups = groupStageCalls([
    call(1, "merge_failure", { level: 2 }),
    call(0, "merge_failure", { level: 1 }),
  ]);

  assert.equal(groups.length, 1);
  assert.deepEqual(groups[0].calls.map((c) => c.level), [1, 2]);
});

test("a stage this page does not know is kept rather than dropped", () => {
  const groups = groupStageCalls([call(0, "merge_failure"), call(1, "slow_update")]);

  assert.equal(groups.at(-1).key, "other");
  assert.equal(groups.at(-1).calls[0].stage, "slow_update");
});

test("a step with no recorded stages produces nothing to show", () => {
  assert.deepEqual(groupStageCalls([]), []);
  assert.deepEqual(groupStageCalls(undefined), []);
});

test("the summary prefers the model's own reasoning", () => {
  assert.equal(
    summarise(call(0, "merge_final", { output: { reasoning: "kept the stricter rule" } })),
    "kept the stricter rule",
  );
});

test("an error is the summary, because it explains the empty result below it", () => {
  const errored = call(0, "merge_final", { error: "APITimeoutError: timed out" });
  assert.equal(summarise(errored), "APITimeoutError: timed out");
});

test("without reasoning, the count of what came out is the next best line", () => {
  assert.equal(
    summarise(call(0, "merge_failure", { output: { edits: [1, 2, 3] } })),
    "3 edits came out of this stage.",
  );
  assert.equal(
    summarise(call(0, "merge_failure", { output: { edits: [1] } })),
    "1 edit came out of this stage.",
  );
});

test("ranking answers in indices rather than edits, and is still counted", () => {
  assert.equal(editCount({ selected_indices: [3, 0] }), 2);
});

test("a call that returned nothing says so instead of showing a blank", () => {
  assert.equal(summarise(call(0, "ranking", { output: null })), "This stage returned nothing to show.");
});

test("a merge round is labelled by its round, and a lone call not at all", () => {
  assert.equal(callLabel({ level: 2 }, 1, 3), "round 2");
  assert.equal(callLabel({ level: null }, 0, 1), "");
  assert.equal(callLabel({ level: null }, 1, 2), "call 2");
});
