import test from "node:test";
import assert from "node:assert/strict";

import { failureText, gateLabel } from "./optimize_gate_label.js";

// The verdict is the one thing on this page a reader acts on: it says whether
// the edit in front of them is in the skill they are about to download. Two
// failure modes matter. Saying too little — "reject", which is what the chart
// used to print — leaves them to guess why. Saying the wrong thing is worse:
// a step whose validation split never came back has not been judged at all, and
// a reader told their edit was "rejected" will go and rewrite an edit that was
// never the problem.

test("a new best says so, and says what it is", () => {
  const v = gateLabel({ gate_action: "accept_new_best", best_score: 0.84 });

  assert.equal(v.tone, "success");
  assert.equal(v.short, "accepted · new best");
  assert.match(v.detail, /84%/);
});

test("a rejection on score names the number it had to beat", () => {
  const v = gateLabel({ gate_action: "reject", gate_reject_reason: "accuracy", best_score: 0.9 });

  assert.equal(v.short, "rejected · score");
  assert.match(v.detail, /90%/);
});

test("a routing rejection names the score it was measured on", () => {
  // The gate compares routing accuracy in a routing run and judge accuracy in
  // an isolated one, and a reader looking at "rejected · score" has to be able
  // to tell which number moved. There is no `activation` reason any more: the
  // guard it named was replaced by measuring the routing itself.
  const v = gateLabel({
    gate_action: "reject", gate_reject_reason: "routing", best_score: 0.62,
  });

  assert.equal(v.short, "rejected · routing");
  assert.match(v.detail, /routing accuracy/i);
  assert.match(v.detail, /62%/);
});

test("an isolated rejection is named for the judge", () => {
  const v = gateLabel({
    gate_action: "reject", gate_reject_reason: "accuracy", best_score: 0.4,
  });

  assert.equal(v.short, "rejected · score");
  assert.match(v.detail, /answer accuracy/i);
});

test("a routing rollout that could not be scored says so, not that the edit lost", () => {
  // Nothing to gate on: no trace landed, or no question in the split carried a
  // skill tag. Reading that as "your edit scored worse" sends the developer to
  // rewrite a description that was never measured.
  const v = gateLabel({
    gate_action: "reject", gate_reject_reason: "routing_unmeasured",
  });

  assert.equal(v.label, "rejected");
  assert.match(v.detail, /could not be measured|nothing to score/i);
  assert.doesNotMatch(v.detail, /worse/);
});

test("a validation split that never came back is not a bad edit", () => {
  const v = gateLabel({
    gate_action: "reject",
    gate_reject_reason: "val_errors",
    val_n_items: 40,
    val_n_scored: 28,
  });

  assert.equal(v.tone, "warning");
  assert.equal(v.short, "rejected · system errors");
  assert.match(v.detail, /12 of 40/);
  assert.match(v.detail, /30%/);
});

test("a skipped step says the training batch was the problem", () => {
  const v = gateLabel({
    gate_action: "skip",
    gate_reject_reason: "train_errors",
    train_n_items: 8,
    train_n_scored: 2,
  });

  assert.equal(v.short, "skipped · system errors");
  assert.match(v.detail, /6 of 8 training questions/);
  assert.match(v.detail, /no validation rollout|bought no validation/);
});

test("the baseline is not an unjudged step", () => {
  const v = gateLabel({ step_no: 0 });

  assert.equal(v.label, "baseline");
  assert.match(v.detail, /no edit to judge/);
});

test("a step still running says so rather than reading as a rejection", () => {
  const v = gateLabel({ step_no: 3 });

  assert.equal(v.label, "not judged");
});

test("an action this module has never heard of is still readable", () => {
  const v = gateLabel({ gate_action: "force_accept" });

  assert.equal(v.label, "force accept");
  assert.equal(v.short, "force accept");
});

test("counts that never arrived do not print NaN", () => {
  const v = gateLabel({ gate_action: "reject", gate_reject_reason: "val_errors" });

  assert.doesNotMatch(v.detail, /NaN|undefined/);
});

test("the failure sentence rounds the share rather than printing a fraction", () => {
  assert.match(failureText({ val_n_items: 3, val_n_scored: 2 }, "val"), /33%/);
});
