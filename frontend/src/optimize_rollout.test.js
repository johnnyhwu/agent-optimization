import test from "node:test";
import assert from "node:assert/strict";

import {
  editsProposed,
  groupResults,
  outcomeOf,
  truncationSummary,
} from "./optimize_rollout.js";

const ok = (key, extra = {}) => ({
  id: key, item_key: key, status: "done", verdict: "correct", judge_score: 1,
  minibatch_no: null, ...extra,
});
const bad = (key, extra = {}) => ok(key, { verdict: "incorrect", judge_score: 0, ...extra });

// --- What happened to one question ------------------------------------------

test("a question that never got an answer is an error, not a wrong answer", () => {
  // `score_rollout` leaves infrastructure failures out of both the numerator
  // and the denominator. A page that painted them the same red as a wrong
  // answer would contradict its own accuracy figure, and the developer would
  // count the reds and find the percentage impossible.
  assert.equal(
    outcomeOf({ status: "failed", failure_kind: "agent_timeout", verdict: null }),
    "error",
  );
});

test("partial credit is neither correct nor incorrect", () => {
  // The soft metric is the average of these scores, so a run whose soft figure
  // sits well above its hard one is being carried by rows like this. Collapsing
  // them into "incorrect" makes that gap unexplainable from the page that is
  // supposed to explain it.
  assert.equal(outcomeOf(bad("q", { judge_score: 0.5 })), "partial");
  assert.equal(outcomeOf(bad("q", { judge_score: 0 })), "incorrect");
  assert.equal(outcomeOf(ok("q")), "correct");
});

test("a question still in flight is pending rather than wrong", () => {
  // The page is open while the rollout runs. Rows arriving as "incorrect"
  // before they have been judged would show a step collapsing and then
  // recovering, every step, for the length of the run.
  assert.equal(outcomeOf({ status: "pending", verdict: null, judge_score: null }), "pending");
});

test("an answered but unjudged question is not a wrong answer either", () => {
  // A missing verdict is the thing that decides this, not the status: a row can
  // carry `done` with nothing in `verdict` if the judge never returned one, and
  // reading that as "incorrect" states a grading outcome that no judge produced.
  assert.equal(outcomeOf({ status: "done", verdict: null, judge_score: null }), "pending");
  assert.equal(outcomeOf({ status: "done", verdict: null, judge_score: 0.8 }), "pending");
});

// --- How the list is grouped -------------------------------------------------

test("training questions are grouped by the analyst call they fed", () => {
  // This grouping is the page's whole claim: these failures were shown to one
  // analyst *together*, and the patch below came from seeing them side by side.
  // Any other grouping is a plausible-looking fiction.
  //
  // The verdicts here deliberately cut across the grouping: batch 1 is a
  // *success* analyst call, so it holds a correct answer, and one wrong answer
  // fed nothing at all. Fixtures where every grouped row is also a failure
  // cannot tell "grouped by minibatch" from "grouped by verdict".
  const groups = groupResults({
    split: "train",
    results: [bad("a", { minibatch_no: 0 }), bad("b", { minibatch_no: 0 }),
              ok("c", { minibatch_no: 1 }), bad("d")],
    minibatches: [
      { minibatch_no: 0, n_items: 2, source_type: "failure" },
      { minibatch_no: 1, n_items: 1, source_type: "success" },
    ],
  });
  assert.deepEqual(groups.map((g) => g.minibatch_no), [0, 1, null]);
  assert.deepEqual(groups[0].results.map((r) => r.item_key), ["a", "b"]);
  assert.deepEqual(groups[1].results.map((r) => r.item_key), ["c"]);
  assert.deepEqual(groups[2].results.map((r) => r.item_key), ["d"]);
});

test("a training question no analyst saw is shown, not dropped", () => {
  // With `failure_only` on, correct answers feed no analyst and carry no
  // minibatch number. They were still rolled out and still paid for, and they
  // are the denominator of the training accuracy on the same page — a list that
  // omitted them would not add up to its own header.
  const groups = groupResults({
    split: "train",
    results: [bad("a", { minibatch_no: 0 }), ok("b")],
    minibatches: [{ minibatch_no: 0, n_items: 1 }],
  });
  assert.equal(groups.length, 2);
  assert.equal(groups.at(-1).minibatch_no, null);
  assert.deepEqual(groups.at(-1).results.map((r) => r.item_key), ["b"]);
});

test("the ungrouped questions come last", () => {
  // The minibatches are the argument; the questions that fed none are context.
  // Putting them first buries the reason the page was opened.
  const groups = groupResults({
    split: "train",
    results: [ok("z"), bad("a", { minibatch_no: 1 })],
    minibatches: [{ minibatch_no: 1, n_items: 1 }],
  });
  assert.deepEqual(groups.map((g) => g.minibatch_no), [1, null]);
});

test("validation is one flat list with no minibatches", () => {
  // Validation is measured and never reflected on. A minibatch heading over
  // held-out questions would imply the edits were derived from them, which is
  // the one thing the gate depends on not being true.
  //
  // Minibatches are passed in anyway, and rows carry numbers: the server
  // already refuses to send them for this split, and this is the second half of
  // that rule rather than a restatement of it. A single-sided guard is one
  // endpoint change away from being no guard.
  const groups = groupResults({
    split: "val",
    results: [ok("a", { minibatch_no: 0 }), bad("b", { minibatch_no: 1 })],
    minibatches: [{ minibatch_no: 0, n_items: 1 }, { minibatch_no: 1, n_items: 1 }],
  });
  assert.equal(groups.length, 1);
  assert.equal(groups[0].minibatch_no, null);
  assert.equal(groups[0].results.length, 2);
});

test("an analyst call whose questions all failed still appears", () => {
  // A minibatch with no rows left to show is not nothing: it is an analyst call
  // that was made and cost money. Dropping empty groups would hide a step that
  // reflected on batches of errors.
  const groups = groupResults({
    split: "train",
    results: [],
    minibatches: [{ minibatch_no: 0, n_items: 3 }],
  });
  assert.deepEqual(groups.map((g) => g.minibatch_no), [0]);
  assert.equal(groups[0].results.length, 0);
});

test("each group counts what went wrong inside it", () => {
  // The header of a group is what makes the list skimmable — "8 items, 5
  // failed" is why a developer opens that one rather than the next.
  const [group] = groupResults({
    split: "val",
    results: [
      ok("a"),
      bad("b"),
      bad("c", { judge_score: 0.5 }),
      { id: "d", item_key: "d", status: "failed", failure_kind: "agent" },
    ],
    minibatches: [],
  });
  assert.equal(group.counts.correct, 1);
  assert.equal(group.counts.incorrect, 1);
  assert.equal(group.counts.partial, 1);
  assert.equal(group.counts.error, 1);
});

// --- The truncation ledger ---------------------------------------------------

test("the truncation summary counts questions, not cuts", () => {
  // One trace can lose several spans, so the ledger holds several entries for
  // the same question. Reporting entry count as "3 traces truncated" when one
  // trace was cut three times overstates how much of the batch was damaged —
  // which is precisely the judgement the line exists to support.
  const summary = truncationSummary({
    n_items: 8,
    chars_before: 41200,
    chars_after: 12000,
    truncation: [
      { item_key: "a", span_index: 1 },
      { item_key: "a", span_index: 4 },
      { item_key: "b", span_index: 2 },
    ],
  });
  assert.equal(summary.itemsTruncated, 2);
  assert.equal(summary.entries, 3);
  assert.equal(summary.before, 41200);
  assert.equal(summary.after, 12000);
});

test("a batch that fitted reports no truncation at all", () => {
  // The cascade measures first and cuts only if it must. "0 truncated" has to
  // be distinguishable from "we did not look", or the reassurance is worthless.
  const summary = truncationSummary({ n_items: 4, chars_before: 900, chars_after: 900 });
  assert.equal(summary.itemsTruncated, 0);
  assert.equal(summary.truncated, false);
});

test("a dropped question is reported separately from a trimmed one", () => {
  // The last stage of the cascade drops whole items rather than cutting more.
  // A question that was never shown to the analyst is a different fact from one
  // that was shown in shortened form, and only the first changes what evidence
  // the patch could possibly rest on.
  const summary = truncationSummary({
    n_items: 8,
    chars_before: 90000,
    chars_after: 12000,
    truncation: [
      { item_key: "a", stage: "tool_result" },
      { item_key: "z", stage: "dropped_item" },
    ],
  });
  assert.deepEqual(summary.dropped, ["z"]);
  assert.equal(summary.itemsTruncated, 2);
});

// --- What the analyst proposed ----------------------------------------------

test("the edit count comes from the patch the analyst returned", () => {
  const n = editsProposed({ raw_output: { patch: { edits: [{}, {}, {}] } } });
  assert.equal(n, 3);
});

test("an analyst call that failed proposed nothing rather than crashing", () => {
  // `raw_output` is NULL when the call errored, and the page renders that row
  // beside the error. Reaching into a null patch takes the whole detail view
  // down with a blank screen instead.
  assert.equal(editsProposed({ raw_output: null, error: "APITimeoutError" }), 0);
  assert.equal(editsProposed({ raw_output: {} }), 0);
});
