import test from "node:test";
import assert from "node:assert/strict";

import {
  Y_FULL,
  accuracyLabel,
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

test("the full range is 0 to 100 with the quarter marks on it", () => {
  // The axis this chart has always had, now one of two modes. It is the honest
  // answer to "how good is it", and the one to fall back on when a fitted axis
  // would be answering a different question.
  assert.deepEqual(Y_FULL, [0, 1]);
  const model = chartModel([baseline(0.51), step(1, { train: 0.52, val: 0.53, best: 0.53 })], {
    width: 400, height: 200, totalSteps: 2, yMode: "full",
  });
  assert.deepEqual(model.yDomain, [0, 1]);
  assert.deepEqual(model.yTicks.map((t) => t.value), [0, 0.25, 0.5, 0.75, 1]);
  assert.deepEqual(model.yTicks.map((t) => t.label), ["0%", "25%", "50%", "75%", "100%"]);
  assert.equal(model.zoomed, false);
});

test("a fitted axis opens up the range the run actually moved in", () => {
  // The complaint this answers: a good run scores between 70 and 100, so on the
  // full range every point it plots is in the top quarter of the plot and three
  // quarters of the picture is empty space under them.
  const steps = [baseline(0.72)];
  for (let n = 1; n <= 6; n += 1) {
    steps.push(step(n, { train: 0.74 + n * 0.01, val: 0.76 + n * 0.02, best: 0.76 + n * 0.02 }));
  }
  const model = chartModel(steps, { width: 400, height: 200, totalSteps: 6, yMode: "fit" });

  const [lo, hi] = model.yDomain;
  assert.ok(lo >= 0.6 && hi <= 1, `expected a tightened domain, got ${lo}–${hi}`);
  // Every plotted value is inside it, including the train series that sits
  // lowest and the best-so-far line that sits highest.
  assert.ok(lo <= 0.72 && hi >= 0.88, `${lo}–${hi} does not contain the data`);
  assert.equal(model.zoomed, true);
  // Five-point marks: an axis labelled 71.3% is a number nobody asked about.
  assert.ok(model.yTicks.every((t) => Number.isInteger(Math.round(t.value * 100))));
  assert.ok(model.yTicks.length >= 3 && model.yTicks.length <= 6);
});

test("fitting never zooms tighter than twenty points", () => {
  // The guard on the whole idea. Fitted to its own data, a run that wobbled
  // between 51% and 53% draws the same dramatic climb as one that gained forty
  // points, and the only way to tell them apart is to read the axis — which is
  // exactly what a picture is for not having to do.
  const model = chartModel([baseline(0.51), step(1, { train: 0.52, val: 0.53, best: 0.53 })], {
    width: 400, height: 200, totalSteps: 2, yMode: "fit",
  });
  const [lo, hi] = model.yDomain;
  assert.ok(hi - lo >= 0.2 - 1e-9, `span was ${hi - lo}`);
});

test("a run whose every score is identical still has an axis with height", () => {
  // Otherwise the domain is a point, the scale divides by zero, and every
  // coordinate is NaN — which SVG renders as nothing at all, with no error.
  const model = chartModel([baseline(0.8), step(1, { train: 0.8, val: 0.8, best: 0.8 })], {
    width: 400, height: 200, totalSteps: 2, yMode: "fit",
  });
  const [lo, hi] = model.yDomain;
  assert.ok(hi - lo >= 0.2 - 1e-9);
  assert.equal(/NaN/.test(JSON.stringify(model)), false);
});

test("a fitted axis stays inside 0 and 100", () => {
  // Accuracy has no meaning outside them, and a plot area whose top eighth can
  // never hold a point is the same wasted space this mode exists to remove — so
  // the widened domain slides back inside rather than hanging over the edge.
  const perfect = chartModel([baseline(0.99), step(1, { train: 1, val: 1, best: 1 })], {
    width: 400, height: 200, totalSteps: 2, yMode: "fit",
  });
  assert.deepEqual(perfect.yDomain[1], 1);
  assert.ok(perfect.yDomain[0] >= 0);

  const hopeless = chartModel([baseline(0.02), step(1, { train: 0.01, val: 0.03, best: 0.03 })], {
    width: 400, height: 200, totalSteps: 2, yMode: "fit",
  });
  assert.equal(hopeless.yDomain[0], 0);
  assert.ok(hopeless.yDomain[1] <= 1);
});

test("an empty run falls back to the full range rather than to nothing", () => {
  const model = chartModel([], { width: 400, height: 200, totalSteps: 6, yMode: "fit" });
  assert.deepEqual(model.yDomain, [0, 1]);
});

test("hiding a series takes it out of the axis it was fitted to", () => {
  // Turning off the train line on a run whose training scores sat well below
  // validation should tighten the axis around what is left. Leaving the hidden
  // series in the domain would keep a band of empty plot where its line used to
  // be — the reader turned it off and the space it occupied stayed.
  const steps = [baseline(0.9), step(1, { train: 0.4, val: 0.92, best: 0.92 })];
  const both = chartModel(steps, { width: 400, height: 200, totalSteps: 1, yMode: "fit" });
  const valOnly = chartModel(steps, {
    width: 400, height: 200, totalSteps: 1, yMode: "fit",
    show: { train: false, val: true, best: true },
  });

  assert.ok(both.yDomain[0] <= 0.4, "the train point must be on the axis while it is shown");
  assert.ok(valOnly.yDomain[0] > 0.4, "hiding it should have tightened the axis");
  assert.deepEqual(valOnly.train, []);
  assert.equal(valOnly.trainPath, "");
  // The series that are still on are untouched.
  assert.equal(valOnly.val.length, both.val.length);
  assert.ok(valOnly.bestPath.length > 0);
});

test("hiding every series leaves an axis rather than a divide by zero", () => {
  const model = chartModel([baseline(0.8), step(1, { train: 0.8, val: 0.8, best: 0.8 })], {
    width: 400, height: 200, totalSteps: 2, yMode: "fit",
    show: { train: false, val: false, best: false },
  });
  assert.deepEqual(model.yDomain, [0, 1]);
  assert.equal(/NaN/.test(JSON.stringify(model)), false);
});

// --- Pixel space ------------------------------------------------------------

test("higher accuracy is higher on the screen", () => {
  // SVG's y axis grows downwards. Forgetting the flip is the single most common
  // way to draw a chart that says the opposite of the truth, and it produces a
  // chart that is otherwise entirely plausible.
  const model = chartModel(
    [baseline(0.0), step(1, { train: 0.5, val: 1.0, best: 1.0 })],
    { width: 400, height: 200, totalSteps: 1, yMode: "full" },
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

// --- The canvas a long run needs --------------------------------------------

test("a short run draws at the width the panel gave it", () => {
  // The canvas only grows when the arithmetic says it must: a ten-step run has
  // room to spare, and widening it would introduce a horizontal scrollbar to
  // reach empty space.
  const steps = [baseline(0.4)];
  for (let n = 1; n <= 10; n += 1) steps.push(step(n, { train: 0.5, val: 0.5 }));
  const model = chartModel(steps, { width: 720, height: 280, totalSteps: 10 });
  assert.equal(model.width, 720);
});

test("a long run widens the canvas rather than shrinking its steps", () => {
  // The complaint: at a fixed width, a sixty-step run gave each step about
  // fourteen screen pixels of click target and a hundred steps gave eight, so
  // pinning the step you meant was a matter of luck. The columns keep a floor
  // and the plot scrolls sideways instead.
  for (const total of [40, 60, 100]) {
    const steps = [baseline(0.4)];
    for (let n = 1; n <= total; n += 1) steps.push(step(n, { train: 0.5, val: 0.5 }));
    const model = chartModel(steps, { width: 720, height: 280, totalSteps: total });

    assert.ok(model.width > 720, `a ${total}-step run should have widened the canvas`);
    const narrowest = Math.min(...model.columns.map((c) => c.width));
    assert.ok(narrowest >= 18, `a ${total}-step run gave a column ${narrowest} units`);
  }
});

test("the widened canvas is still where the model says the steps are", () => {
  // The component turns a pointer position into a coordinate with the width the
  // model reports. If the columns were laid out for one width and the canvas
  // drawn at another, every hover and click on a long run would report a step
  // near the one under the pointer — the most confusing kind of wrong.
  const steps = [baseline(0.4)];
  for (let n = 1; n <= 60; n += 1) steps.push(step(n, { train: 0.5, val: 0.5 }));
  const model = chartModel(steps, { width: 720, height: 280, totalSteps: 60 });

  assert.equal(model.plot.left + model.plot.width, model.width - 14);
  const mid = model.plot.top + model.plot.height / 2;
  for (const stepNo of [0, 1, 37, 60]) {
    const column = model.columns.find((c) => c.stepNo === stepNo);
    assert.equal(model.stepAtPoint(column.cx, mid), stepNo);
    // …and anywhere inside the column, which is the point of having one.
    assert.equal(model.stepAtPoint(column.x + 1, mid), stepNo);
    assert.equal(model.stepAtPoint(column.x + column.width - 1, mid), stepNo);
  }
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

test("a point outside the plot area picks no step at all", () => {
  // `stepAt` clamps by design, which is right for the column and wrong for the
  // margins. The svg is the full canvas: a 38px gutter of accuracy labels down
  // the left and a strip of step numbers along the bottom. Clicking either
  // pinned a step, and sweeping the pointer across the axis labels showed a
  // readout for a step nowhere near the cursor.
  const model = chartModel(
    [baseline(0.4), step(1, { train: 0.5, val: 0.6, best: 0.6 }), step(2, { train: 0.6, val: 0.7, best: 0.7 })],
    { width: 400, height: 200, totalSteps: 2 },
  );
  const { left, top, width, height } = model.plot;
  const midX = left + width / 2;
  const midY = top + height / 2;

  assert.equal(model.stepAtPoint(left - 10, midY), null, "left gutter");
  assert.equal(model.stepAtPoint(left + width + 10, midY), null, "right margin");
  assert.equal(model.stepAtPoint(midX, top - 5), null, "above the plot");
  assert.equal(model.stepAtPoint(midX, top + height + 10), null, "the x-axis labels");

  // Inside, it still answers with the column, exactly as stepAt does.
  const target = model.val.find((p) => p.stepNo === 1);
  assert.equal(model.stepAtPoint(target.x + 3, midY), 1);
  assert.equal(model.stepAtPoint(target.x - 3, midY), 1);
  // The edges of the plot belong to the plot.
  assert.equal(model.stepAtPoint(left, top), 0);
  assert.equal(model.stepAtPoint(left + width, top + height), 2);
});

test("every planned step gets a column, whether or not it has a point", () => {
  // The pin and hover feedback is drawn from these bands rather than from the
  // markers, because the markers are exactly what a troubled run does not have:
  // a rejected candidate is a cross with no circle to stroke, and a step whose
  // validation was skipped as a cache hit has no validation marker at all. Both
  // used to click with no visible effect whatsoever.
  const model = chartModel(
    [
      baseline(0.4),
      // Rejected: drawn as a cross.
      step(1, { train: 0.5, val: 0.6, gate: "reject", best: 0.4 }),
      // Validation skipped: no val point exists for this step.
      step(2, { train: 0.6, val: null, gate: "reject", best: 0.4 }),
    ],
    { width: 400, height: 200, totalSteps: 4 },
  );

  // One per step across the whole *planned* run, not just the measured part —
  // the axis is sized for the plan, so the columns have to be too or the ones
  // past the end would be unclickable dead space.
  assert.deepEqual(model.columns.map((c) => c.stepNo), [0, 1, 2, 3, 4]);

  // A step with no validation marker still has a band to highlight.
  const skipped = model.columns.find((c) => c.stepNo === 2);
  assert.ok(skipped.width > 0);
  assert.equal(model.val.some((p) => p.stepNo === 2), false);

  // Bands tile the axis: each one starts where the last ended, and the centre
  // of each is the x its markers are drawn at.
  for (let i = 1; i < model.columns.length; i += 1) {
    const previous = model.columns[i - 1];
    const current = model.columns[i];
    assert.ok(
      Math.abs(previous.x + previous.width - current.x) < 0.001,
      `column ${current.stepNo} should start where ${previous.stepNo} ended`,
    );
  }
  const one = model.columns.find((c) => c.stepNo === 1);
  const marker = model.val.find((p) => p.stepNo === 1);
  assert.ok(Math.abs(one.cx - marker.x) < 0.001);

  // And the band is the click target the model already reports.
  assert.equal(model.stepAtPoint(one.cx, model.plot.top + 5), 1);
});

// --- Which accuracy the chart is drawing ------------------------------------
//
// A routing run records two numbers per step: whether the agent opened the
// skills each question was tagged for, and whether the judge liked the answers.
// The gate compares the first. Drawing the second on the same axis without
// saying which is which would put a line the run is not optimising next to the
// one it is, at the same weight.

test("a routing run plots the score its gate compares", () => {
  const steps = [
    { step_no: 0, val_hard: 0.9, val_routing_hard: 0.4 },
    { step_no: 1, val_hard: 0.8, val_routing_hard: 0.6, train_hard: 0.7, train_routing_hard: 0.5 },
  ];

  const { val, train } = series(steps, "hard", { mode: "routing" });

  assert.deepEqual(val.map((p) => p.value), [0.4, 0.6]);
  assert.deepEqual(train.map((p) => p.value), [0.5]);
});

test("an isolated run plots the judge, exactly as it did before", () => {
  const steps = [
    { step_no: 0, val_hard: 0.9, val_routing_hard: 0.4 },
    { step_no: 1, val_hard: 0.8, val_routing_hard: 0.6, train_hard: 0.7, train_routing_hard: 0.5 },
  ];

  const { val } = series(steps, "hard", { mode: "isolated" });

  assert.deepEqual(val.map((p) => p.value), [0.9, 0.8]);
});

test("a routing step whose routing score is missing contributes no point", () => {
  // Not a zero. A step whose validation split could not be scored draws nothing
  // rather than a collapse to the floor, which is what a destroyed skill looks
  // like and is the one thing a reader must not confuse it with.
  //
  // The baseline carries a routing score so this stays a run that *records*
  // routing: a run with the column null on every step is a run from before
  // migration 0016, which falls back to the judge on purpose (below). One
  // unmeasured step among measured ones is the case this test is about.
  const steps = [
    { step_no: 0, val_hard: 0.9, val_routing_hard: 0.4 },
    { step_no: 1, val_hard: 0.8, val_routing_hard: null },
  ];

  assert.deepEqual(series(steps, "hard", { mode: "routing" }).val.slice(1), []);
});

test("the axis says which accuracy it is showing", () => {
  assert.match(accuracyLabel("routing"), /routing/i);
  assert.match(accuracyLabel("isolated"), /answer/i);
});

// --- runs recorded before routing accuracy existed ---------------------------
//
// Migration 0016 added the routing columns. A routing run created before it has
// them NULL on every step, and `series` drops null points — so switching on
// `run.mode` alone left those runs with two empty lines under an axis reading
// "Routing accuracy", with nothing on screen saying why.

test("a routing run recorded before 0016 plots the judge rather than nothing", () => {
  const steps = [
    { step_no: 0, val_hard: 0.9, val_routing_hard: null },
    { step_no: 1, val_hard: 0.8, train_hard: 0.7, val_routing_hard: null, train_routing_hard: null },
  ];

  const { val, train } = series(steps, "hard", { mode: "routing" });

  assert.deepEqual(val.map((p) => p.value), [0.9, 0.8]);
  assert.deepEqual(train.map((p) => p.value), [0.7]);
});

test("the axis says the fallback is the older measurement", () => {
  // Plotting the judge's numbers for these runs *silently* would be its own
  // lie: it is not what their gate compared. The label has to carry that.
  const legacy = [{ step_no: 0, val_hard: 0.9, val_routing_hard: null }];
  const model = chartModel(legacy, { mode: "routing" });

  assert.equal(model.legacyMetric, true);
  assert.notEqual(accuracyLabel("routing", { legacy: true }), accuracyLabel("routing"));
  assert.match(accuracyLabel("routing", { legacy: true }), /answer/i);
});

test("a routing run with any routing score at all does not fall back", () => {
  // One recorded step is enough to know the run is a real routing run, and its
  // unmeasured steps must keep drawing nothing rather than the judge's line.
  const steps = [
    { step_no: 0, val_hard: 0.9, val_routing_hard: 0.4 },
    { step_no: 1, val_hard: 0.8, val_routing_hard: null },
  ];

  const model = chartModel(steps, { mode: "routing" });

  assert.equal(model.legacyMetric, false);
  assert.deepEqual(series(steps, "hard", { mode: "routing" }).val.map((p) => p.value), [0.4]);
});

test("a run with no scores of any kind is not mistaken for a legacy run", () => {
  // A routing run that has only just started has both families null. It has not
  // told us it predates 0016 — it has told us nothing yet — and labelling its
  // empty chart "Answer accuracy" would be the same silent substitution in the
  // other direction.
  const model = chartModel([{ step_no: 0, val_hard: null, val_routing_hard: null }], {
    mode: "routing",
  });

  assert.equal(model.legacyMetric, false);
});

// --- the axis is fitted to the line that is drawn ---------------------------

test("a routing run fits its y axis to the routing scores it plots", () => {
  // `visibleValues` read the judge's columns whatever the mode while `series`
  // plotted routing, so a run whose judge sat at 0.85-0.90 and whose routing sat
  // at 0.30-0.45 fitted the axis to the first and drew the second outside it.
  const steps = [
    { step_no: 0, val_hard: 0.88, val_routing_hard: 0.3 },
    { step_no: 1, val_hard: 0.9, val_routing_hard: 0.45, train_hard: 0.86, train_routing_hard: 0.35 },
  ];

  const model = chartModel(steps, { mode: "routing", yMode: "fit" });
  const [lo, hi] = model.yDomain;

  assert.ok(lo <= 0.3, `y axis starts at ${lo}, above the lowest plotted point`);
  assert.ok(hi >= 0.45, `y axis ends at ${hi}, below the highest plotted point`);
});
