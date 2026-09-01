import test from "node:test";
import assert from "node:assert/strict";
import { analystCallsPerStep, estimateRun, explainRun } from "./optimize_cost.js";

// What the wizard's last step promises before anyone presses Start.
//
// Stated in calls, never in money. This platform never sees a price list — the
// models are whatever base URL the developer pointed it at — and a number with
// a currency symbol on it would be believed in a way a wrong number should not
// be. Calls are something we can actually count.

const base = { nTrain: 20, nVal: 10, epochs: 1, batchSize: 5, minibatchSize: 4 };

test("the schedule is the training split divided into batches, once per epoch", () => {
  const e = estimateRun({ ...base, epochs: 3 });
  assert.equal(e.stepsPerEpoch, 4);
  assert.equal(e.totalSteps, 12);
});

test("a split that does not divide evenly still trains on all of it", () => {
  // 21 questions in batches of 5 is five steps, the last one short — not four.
  // Flooring drops the remainder, so the estimate quietly plans a run that
  // never shows the last few questions to the optimizer at all, and the number
  // on the Review screen is wrong in the direction that looks cheaper.
  const e = estimateRun({ ...base, nTrain: 21, batchSize: 5 });
  assert.equal(e.stepsPerEpoch, 5);
  assert.equal(e.totalSteps, 5);
});

test("a batch larger than the split is one step, not a fractional one", () => {
  // Setting the batch size to the whole split is the documented way to make one
  // epoch one step. `ceil` of a fraction below 1 has to land on 1, or the run
  // is planned as zero steps and the estimate says the run does nothing.
  const e = estimateRun({ ...base, nTrain: 8, batchSize: 40 });
  assert.equal(e.stepsPerEpoch, 1);
  assert.equal(e.totalSteps, 1);
});

test("every step answers its batch and the whole validation split, plus one baseline", () => {
  // The baseline is the step-0 measurement, validation only. Forgetting it
  // understates a short run by a noticeable share — with 2 steps it is a third
  // of the validation work.
  const e = estimateRun(base);
  assert.equal(e.stepsPerEpoch, 4);
  assert.equal(e.agentCalls, 10 + 4 * (5 + 10));
});

test("a batch is capped by the split it is drawn from", () => {
  // Asking for 40 questions from a split of 8 does not answer 40 of them. An
  // uncapped estimate scales with a number the user typed rather than with the
  // work, and is wrong by a factor rather than by a little.
  const e = estimateRun({ ...base, nTrain: 8, batchSize: 40 });
  assert.equal(e.agentCalls, 10 + 1 * (8 + 10));
});

test("every answered question is judged exactly once", () => {
  // The judge is the other per-question cost and it is easy to leave out of a
  // plan, which then understates the whole run by half.
  const e = estimateRun(base);
  assert.equal(e.judgeCalls, e.agentCalls);
});

test("the optimizer's calls scale with the minibatch split, not with the batch", () => {
  // The analyst runs once per minibatch, and the minibatch size is a separate
  // setting people forget is there. Then one merge and one ranking per step.
  // Failures and successes are split into their own groups, so a batch can
  // produce one more group than a single division would suggest — the estimate
  // takes the worst case, because a plan that undersells the expensive model is
  // the one that matters.
  const e = estimateRun({ ...base, batchSize: 8, minibatchSize: 4, nTrain: 8, epochs: 1 });
  assert.equal(e.stepsPerEpoch, 1);
  assert.equal(e.optimizerCallsMax, 2 + 1 + 2);
});

test("a minibatch bigger than the batch still splits failures from successes", () => {
  // The tempting simplification is that an oversized minibatch means one
  // analyst call. In isolated mode it does not: a batch with one right answer
  // and one wrong one becomes two groups whatever the size, because the two are
  // reflected on with different prompts. Estimating one call here understates
  // the most expensive model in the run on every single step.
  const e = estimateRun({ ...base, nTrain: 4, batchSize: 4, minibatchSize: 50 });
  assert.equal(e.optimizerCallsMax, 2 + 2);
});

test("routing is one analyst call a step, and no merge or ranking", () => {
  // The split above is what routing gave up: a description is one line, so
  // every group would return a complete rewrite of it and the merge would pick
  // between them blind. One call means nothing to merge and nothing to rank,
  // and both stages return their input untouched without calling the model.
  const e = estimateRun({
    ...base, nTrain: 40, batchSize: 8, minibatchSize: 8, epochs: 1, mode: "routing",
  });
  assert.equal(e.stepsPerEpoch, 5);
  assert.equal(e.optimizerCallsMax, 5);
});

test("routing ignores the minibatch size entirely", () => {
  const small = estimateRun({ ...base, nTrain: 8, batchSize: 8, minibatchSize: 1, mode: "routing" });
  const large = estimateRun({ ...base, nTrain: 8, batchSize: 8, minibatchSize: 50, mode: "routing" });
  assert.equal(small.optimizerCallsMax, large.optimizerCallsMax);
});

test("analystCallsPerStep is one for routing whatever it is given", () => {
  assert.equal(analystCallsPerStep(40, 4, "routing"), 1);
  assert.equal(analystCallsPerStep(40, 4, "isolated"), 11);
  // No questions is still no call.
  assert.equal(analystCallsPerStep(0, 4, "routing"), 0);
});

test("the estimate degrades to zero rather than to NaN when the wizard is half-filled", () => {
  // The Review step renders while its own inputs are still being typed, and a
  // blank number field reads as "". `NaN` would be printed on the page as the
  // last thing a user sees before pressing Start.
  for (const e of [
    estimateRun({}),
    estimateRun({ nTrain: 0, nVal: 0, epochs: 0, batchSize: 0, minibatchSize: 0 }),
    estimateRun({ nTrain: "", nVal: "", epochs: "", batchSize: "", minibatchSize: "" }),
  ]) {
    for (const [key, value] of Object.entries(e)) {
      assert.ok(Number.isFinite(value), `${key} = ${value}`);
      assert.ok(value >= 0, `${key} = ${value}`);
    }
  }
});

test("questions answered is the agent count, named for what a reader is picturing", () => {
  // Two numbers that happen to be equal today for a reason worth stating: one
  // agent call per question, one question per agent call.
  const e = estimateRun(base);
  assert.equal(e.questionsAnswered, e.agentCalls);
});

test("every number on the review card can be checked by the reader", () => {
  // The `?` beside each count. A derivation that disagreed with the number
  // beside it would be worse than none, so it is generated from the same inputs
  // rather than written out by hand next to the estimate.
  const e = estimateRun(base);
  const x = explainRun(base);
  assert.ok(x.steps.includes(String(e.totalSteps)));
  assert.ok(x.agentCalls.includes(e.agentCalls.toLocaleString()));
  assert.ok(x.judgeCalls.includes(e.judgeCalls.toLocaleString()));
  assert.ok(x.optimizerCalls.includes(e.optimizerCallsMax.toLocaleString()));
});

test("the agent explanation accounts for the baseline as well as the steps", () => {
  // "Does that include validation?" is the question the bare number always
  // draws, and the baseline pass is the part nobody expects.
  const x = explainRun(base);
  assert.match(x.agentCalls, /baseline/);
  assert.match(x.agentCalls, /10 \+ 4 × \(5 \+ 10\)/);
});

test("a half-filled wizard still explains itself without NaN", () => {
  for (const x of Object.values(explainRun({}))) {
    assert.ok(x.length > 0);
    assert.ok(!x.includes("NaN"), x);
  }
});
