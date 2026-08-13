import test from "node:test";
import assert from "node:assert/strict";

import {
  Y_DOMAIN,
  bestSoFar,
  chartModel,
  epochBands,
  series,
  xDomain,
} from "./optimize_chart.js";

// The chart is the page. A developer decides from it whether an hour of agent
// calls bought anything, and every failure guarded below is one where the chart
// still looks perfectly ordinary while saying something untrue.

const baseline = (val, extra = {}) => ({
  step_no: 0,
  epoch_no: 0,
  step_in_epoch: 0,
  status: "done",
  gate_action: null,
  train_hard: null,
  val_hard: val,
  best_score: val,
  ...extra,
});

const step = (n, { epoch = 1, train = null, val = null, gate = "accept_new_best", best, ...extra } = {}) => ({
  step_no: n,
  epoch_no: epoch,
  step_in_epoch: n,
  status: "done",
  gate_action: gate,
  train_hard: train,
  val_hard: val,
  best_score: best,
  ...extra,
});

// --- Where the two series sit on the x axis ---------------------------------

test("a train point sits half a step to the left of its own step", () => {
  // Train is measured with the skill as it *entered* the step; validation with
  // the candidate the step produced. They are two different skills. Drawing
  // both at the same x makes the chart claim the edit was measured before it
  // was made, and a train/val gap then reads as overfitting rather than as the
  // step having done something.
  const { train, val } = series([baseline(0.4), step(1, { train: 0.5, val: 0.6, best: 0.6 })]);
  assert.equal(train.length, 1);
  assert.equal(train[0].x, 0.5);
  assert.equal(val.at(-1).x, 1);
});

test("the baseline is drawn at x=0 and never gets a train point", () => {
  // Step 0 buys no training rollout — there is no candidate to compare against
  // yet. A baseline plotted at x=1 shifts every later point right by one and
  // silently attributes the initial score to the first step's edit.
  //
  // The baseline is given a training number here that the engine never
  // produces, because the rule has to be "step 0 has no train point" and not
  // "step 0 happens to have nothing to draw": a train point for step 0 would
  // land at x = −0.5, off the left edge of the axis, claiming a measurement of
  // the initial skill that the run did not pay for.
  const { train, val } = series([
    baseline(0.4, { train_hard: 0.9 }),
    step(1, { train: 0.5, val: 0.6, best: 0.6 }),
  ]);
  assert.deepEqual(val.map((p) => p.x), [0, 1]);
  assert.deepEqual(train.map((p) => p.stepNo), [1]);
});

test("a step still running contributes no point rather than a zero", () => {
  // A rollout in flight has null accuracy. Coercing null to 0 draws a cliff to
  // the floor on every step while it runs, which is indistinguishable from the
  // edit having destroyed the skill.
  const { train, val } = series([baseline(0.4), step(1, { train: null, val: null, gate: null })]);
  assert.equal(train.length, 0);
  assert.deepEqual(val.map((p) => p.stepNo), [0]);
});

test("a step measured on training but not yet validated plots the train point alone", () => {
  // The two rollouts of a step finish minutes apart. Waiting for both before
  // drawing either would leave the chart frozen for most of the run.
  const { train, val } = series([baseline(0.4), step(1, { train: 0.55, val: null, gate: null })]);
  assert.deepEqual(train.map((p) => p.x), [0.5]);
  assert.deepEqual(val.map((p) => p.stepNo), [0]);
});

test("the soft metric reads the soft columns, not the hard ones", () => {
  // Partial credit and strict correctness are different numbers on the same
  // chart. Reading `*_hard` regardless of the toggle makes the switch a no-op
  // that looks like the two metrics happening to agree.
  const steps = [
    baseline(0.4, { val_soft: 0.7 }),
    step(1, { train: 0.5, val: 0.6, best: 0.6, train_soft: 0.8, val_soft: 0.9 }),
  ];
  const { train, val } = series(steps, "soft");
  assert.equal(train[0].value, 0.8);
  assert.deepEqual(val.map((p) => p.value), [0.7, 0.9]);
});

// --- What the gate did to each validation point -----------------------------

test("a rejected step still gets a validation point", () => {
  // Most steps of a healthy run are rejected. Plotting only the accepted ones
  // draws a line that never falls and hides the evidence that the optimizer is
  // proposing edits that do not work.
  const { val } = series([
    baseline(0.4),
    step(1, { train: 0.5, val: 0.3, gate: "reject", best: 0.4 }),
  ]);
  assert.equal(val.length, 2);
  assert.equal(val.at(-1).state, "rejected");
  assert.equal(val.at(-1).value, 0.3);
});

test("accepted, rejected and baseline are three different states", () => {
  // The marker style is the only thing on the chart that says whether an edit
  // was kept. One state collapsed into another makes the run's whole shape a
  // guess.
  const { val } = series([
    baseline(0.4),
    step(1, { train: 0.5, val: 0.6, gate: "accept_new_best", best: 0.6 }),
    step(2, { train: 0.6, val: 0.5, gate: "reject", best: 0.6 }),
    step(3, { train: 0.6, val: 0.6, gate: "accept", best: 0.6 }),
  ]);
  assert.deepEqual(val.map((p) => p.state), ["baseline", "accepted", "rejected", "accepted"]);
});

test("the best step is marked, and only that one", () => {
  // The download defaults to best-by-validation. If the chart rings a different
  // point than the one the zip contains, the developer ships a skill they did
  // not choose.
  //
  // Both steps here set a new best as they happen, which is the case that
  // separates "is the best" from "was the best when it ran" — a run that
  // improves twice has two `accept_new_best` steps and exactly one best.
  const { val } = series(
    [
      baseline(0.4),
      step(1, { train: 0.5, val: 0.7, gate: "accept_new_best", best: 0.7 }),
      step(2, { train: 0.7, val: 0.8, gate: "accept_new_best", best: 0.8 }),
    ],
    "hard",
    { bestStep: 2 },
  );
  assert.deepEqual(val.map((p) => p.isBest), [false, false, true]);
});

// --- The best-so-far line, which is the gate's own threshold -----------------

test("the best-so-far line holds flat across a rejection", () => {
  // This line is what the gate actually compares against. If it tracked the
  // latest validation score instead, it would dip on every rejected step — and
  // a developer reading the chart would conclude the run was getting worse when
  // in fact nothing was kept and nothing was lost.
  const points = bestSoFar([
    baseline(0.4),
    step(1, { train: 0.5, val: 0.7, best: 0.7 }),
    step(2, { train: 0.7, val: 0.2, gate: "reject", best: 0.7 }),
    step(3, { train: 0.7, val: 0.3, gate: "reject", best: 0.7 }),
  ]);
  assert.deepEqual(points.map((p) => p.value), [0.4, 0.7, 0.7, 0.7]);
});

test("the best-so-far line never falls", () => {
  // A monotonic line is the whole claim being made. Any implementation that
  // could produce a decrease is reading the wrong column.
  const points = bestSoFar([
    baseline(0.6),
    step(1, { train: 0.5, val: 0.2, gate: "reject", best: 0.6 }),
    step(2, { train: 0.5, val: 0.9, gate: "accept_new_best", best: 0.9 }),
  ]);
  const values = points.map((p) => p.value);
  assert.deepEqual(values, [0.6, 0.6, 0.9]);
  values.forEach((v, i) => i && assert.ok(v >= values[i - 1], `fell at ${i}`));
});

test("a step with no gate verdict yet does not extend the best-so-far line", () => {
  // Carrying the line forward to a step that has not been judged draws a
  // threshold the gate has not applied.
  const points = bestSoFar([
    baseline(0.4),
    step(1, { train: 0.5, val: null, gate: null, best: undefined }),
  ]);
  assert.deepEqual(points.map((p) => p.stepNo), [0]);
});

// --- Epoch bands ------------------------------------------------------------

test("an epoch band spans from its first train point to its last validation point", () => {
  // The band exists to say which batches belong to one pass over the data. A
  // band starting at the step number instead of half a step earlier leaves the
  // epoch's first train point outside its own band; ending at the step number
  // leaves the last validation point outside.
  const bands = epochBands([
    baseline(0.4),
    step(1, { epoch: 1, train: 0.5, val: 0.5 }),
    step(2, { epoch: 1, train: 0.5, val: 0.5 }),
    step(3, { epoch: 2, train: 0.5, val: 0.5 }),
    step(4, { epoch: 2, train: 0.5, val: 0.5 }),
  ]);
  assert.deepEqual(bands, [
    { epochNo: 1, x0: 0.5, x1: 2.5 },
    { epochNo: 2, x0: 2.5, x1: 4.5 },
  ]);
});

test("the baseline is not an epoch", () => {
  // Step 0 carries epoch_no 0 from the engine. Shading it as an epoch would
  // claim a pass over the training data that never happened — the baseline runs
  // on validation only.
  const bands = epochBands([baseline(0.4), step(1, { epoch: 1, train: 0.5, val: 0.5 })]);
  assert.deepEqual(bands.map((b) => b.epochNo), [1]);
});

test("bands are derived from the steps that exist, not from the planned count", () => {
  // A run cancelled or interrupted mid-epoch has fewer steps than it planned.
  // Bands computed as `epoch * steps_per_epoch` would shade past the end of the
  // data and imply work that was never done.
  const bands = epochBands([
    baseline(0.4),
    step(1, { epoch: 1, train: 0.5, val: 0.5 }),
    step(2, { epoch: 1, train: 0.5, val: 0.5 }),
    step(3, { epoch: 2, train: 0.5, val: 0.5 }),
  ]);
  assert.deepEqual(bands, [
    { epochNo: 1, x0: 0.5, x1: 2.5 },
    { epochNo: 2, x0: 2.5, x1: 3.5 },
  ]);
});

// --- The axes ---------------------------------------------------------------

test("the x axis is sized for the whole planned run, not for what has finished", () => {
  // The overview is open while the run executes. An axis fitted to the steps so
  // far rescales on every one of them, so every existing point slides sideways
  // each time a new one lands and the chart is unreadable while it matters most.
  const early = xDomain([baseline(0.4), step(1, { train: 0.5, val: 0.6 })], 12);
  const later = xDomain(
    [baseline(0.4), step(1, { train: 0.5, val: 0.6 }), step(2, { train: 0.6, val: 0.6 })],
    12,
  );
  assert.deepEqual(early, later);
  assert.equal(early[1], 12.5);
});

test("a run that overran its planned step count still fits on the axis", () => {
  // total_steps is what was planned; a resumed run can hold more rows than that.
  // An axis that ignored the data would draw those points off the right edge.
  assert.deepEqual(xDomain([baseline(0.4), step(9, { train: 0.5, val: 0.6 })], 4), [-0.5, 9.5]);
});

test("the y axis is always the full accuracy range", () => {
  // Auto-scaling accuracy to the data turns a two-point wobble into a dramatic
  // climb. The question the chart answers is "how good is it", and that is only
  // legible against 0 and 100.
  assert.deepEqual(Y_DOMAIN, [0, 1]);
  const model = chartModel([baseline(0.51), step(1, { train: 0.52, val: 0.53, best: 0.53 })], {
    width: 400,
    height: 200,
    totalSteps: 2,
  });
  assert.deepEqual(model.yTicks.map((t) => t.value), [0, 0.25, 0.5, 0.75, 1]);
});

// --- Pixel space ------------------------------------------------------------

test("higher accuracy is higher on the screen", () => {
  // SVG's y axis grows downwards. Forgetting the flip is the single most common
  // way to draw a chart that says the opposite of the truth, and it produces a
  // chart that is otherwise entirely plausible.
  const model = chartModel(
    [baseline(0.0), step(1, { train: 0.5, val: 1.0, best: 1.0 })],
    { width: 400, height: 200, totalSteps: 1 },
  );
  const [low, high] = model.val;
  assert.ok(high.y < low.y, `expected ${high.y} above ${low.y}`);
  assert.equal(high.y, model.plot.top);
  assert.equal(low.y, model.plot.top + model.plot.height);
});

test("the plot area leaves room for the axis labels", () => {
  // Points drawn from x=0 sit under the y-axis labels, which reads as the first
  // step being cut off by the panel edge.
  const model = chartModel([baseline(0.4)], { width: 400, height: 200, totalSteps: 4 });
  assert.ok(model.plot.left > 0);
  assert.ok(model.plot.left + model.plot.width <= 400);
  assert.ok(model.plot.top + model.plot.height < 200);
});

test("an empty run produces an empty chart rather than NaN coordinates", () => {
  // A run is created and rendered before its first step finishes. NaN in a path
  // makes SVG drop the whole element, so the failure is an invisible chart with
  // no error anywhere.
  const model = chartModel([], { width: 400, height: 200, totalSteps: 6 });
  assert.deepEqual(model.train, []);
  assert.deepEqual(model.val, []);
  assert.equal(model.trainPath, "");
  assert.equal(model.bestPath, "");
  assert.ok(model.bands.every((b) => Number.isFinite(b.x) && Number.isFinite(b.width)));
  assert.equal(/NaN/.test(JSON.stringify(model)), false);
});

test("the paths join the points that were plotted, in step order", () => {
  // A path built from the raw step list rather than from the plotted points
  // draws a line through steps that have no measurement — a segment to nowhere.
  const model = chartModel(
    [
      baseline(0.4),
      step(1, { train: 0.5, val: 0.6, best: 0.6 }),
      step(2, { train: 0.6, val: null, gate: null }),
    ],
    { width: 400, height: 200, totalSteps: 4 },
  );
  assert.equal(model.trainPath.match(/[ML]/g).length, 2);
  assert.equal(model.valPath.match(/[ML]/g).length, 2);
});

test("the best-so-far path is stepped, not sloped", () => {
  // Drawing it as a straight line between two scores claims the threshold rose
  // gradually across the step. The gate is a comparison against one number that
  // changes at an instant, and a sloped line invites reading an improvement out
  // of a step where nothing was accepted.
  const model = chartModel(
    [baseline(0.4), step(1, { train: 0.5, val: 0.8, best: 0.8 })],
    { width: 400, height: 200, totalSteps: 2 },
  );
  // Two points, four commands: the horizontal carry then the vertical rise.
  const commands = model.bestPath.match(/[ML]/g);
  assert.equal(commands.length, 3);
  const ys = [...model.bestPath.matchAll(/[ML]([\d.]+),([\d.]+)/g)].map((m) => Number(m[2]));
  assert.equal(ys[0], ys[1]); // holds flat until the new best is reached
  assert.ok(ys[2] < ys[1]); // then rises
});

test("x ticks are whole steps and the baseline is labelled as one", () => {
  // "Step 0" is not a step, and a developer looking for the initial score does
  // not find it under a number.
  const model = chartModel(
    [baseline(0.4), step(1, { train: 0.5, val: 0.6, best: 0.6 })],
    { width: 400, height: 200, totalSteps: 2 },
  );
  assert.deepEqual(model.xTicks.map((t) => t.label), ["base", "1", "2"]);
});

test("the tick count stays readable on a long run", () => {
  // One tick per step on a sixty-step run overlaps into a grey smear. Thinning
  // has to keep the ends, which are the two labels anyone actually reads.
  //
  // 61 steps, not 60: a length that divides evenly by the stride cannot tell
  // whether the last tick is drawn deliberately or lands there by luck, and
  // "how far along is it" is read off exactly that label.
  const steps = [baseline(0.4)];
  for (let n = 1; n <= 61; n += 1) steps.push(step(n, { epoch: Math.ceil(n / 20), train: 0.5, val: 0.5 }));
  const model = chartModel(steps, { width: 600, height: 200, totalSteps: 61 });
  assert.ok(model.xTicks.length <= 16, `got ${model.xTicks.length} ticks`);
  assert.equal(model.xTicks[0].label, "base");
  assert.equal(model.xTicks.at(-1).label, "61");
  // …and the tick before it is dropped rather than left to collide with it.
  assert.ok(61 - model.xTicks.at(-2).stepNo > 1);
});

// --- Finding the step behind a click ----------------------------------------

test("clicking near a point picks that step", () => {
  // The click target has to be the whole column, not the 4px circle: a chart
  // where only a perfect hit responds reads as broken rather than as precise.
  const model = chartModel(
    [baseline(0.4), step(1, { train: 0.5, val: 0.6, best: 0.6 }), step(2, { train: 0.6, val: 0.7, best: 0.7 })],
    { width: 400, height: 200, totalSteps: 2 },
  );
  // A step in the middle, deliberately: at the last step the clamp to the axis
  // end would return the right answer even from an implementation that never
  // rounded at all.
  const target = model.val.find((p) => p.stepNo === 1);
  assert.equal(model.stepAt(target.x + 3), 1);
  assert.equal(model.stepAt(target.x - 3), 1);
  // Off the plot entirely still resolves to the nearest step rather than to
  // nothing — a drag that leaves the axis should not blank the pinned card.
  assert.equal(model.stepAt(model.plot.left - 50), 0);
  assert.equal(model.stepAt(model.plot.left + model.plot.width + 50), 2);
});
