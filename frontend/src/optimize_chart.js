// The overview chart's arithmetic, with no React and no SVG in it.
//
// It lives apart from the component for one reason: this is where the chart can
// lie. Every mistake below produces a picture that looks entirely ordinary —
// a train line shifted half a step, a threshold that dips on a rejected step,
// an axis that rescales while the run streams — and a developer reads a
// conclusion off it that the numbers do not support. Pure functions are the
// only part of this repo's frontend that can be tested (`node --test` over
// `src/*.js`), so the part that can lie is the part that lives here.
//
// Two conventions carry the whole design:
//
//   * **Train is measured before the edit, validation after it.** They are two
//     different skills, so the train point of step k sits at `x = k − 0.5` and
//     the validation point at `x = k`. Step 0 is the baseline: validation only.
//   * **The best-so-far line is the gate's own threshold**, not a smoothing of
//     the validation series. It is a staircase, it never falls, and a candidate
//     is accepted exactly when its point clears it.
//
// Two of the three things a reader can now change about the picture also live
// here, for the same reason — each of them can produce a chart that is
// plausible and wrong. A fitted y axis can turn three points of noise into a
// climb, so `yDomain` refuses to zoom tighter than twenty points and snaps to
// marks a reader recognises. Hiding a series has to change the axis it was
// fitted to, or the plot keeps a band of empty space where the hidden line
// used to be. The third — the canvas growing so that every step stays big
// enough to click — is `minStepWidth`, and it is here because the width it
// returns is also what the component must map the pointer through.

export const Y_FULL = [0, 1];

// Which series a reader can turn off, and the default: all of them.
export const ALL_SERIES = { train: true, val: true, best: true };

// Enough ticks to locate a step, few enough to stay legible at panel width.
const MAX_X_TICKS = 16;

// The fitted axis snaps to five-point marks. Accuracy is read as a percentage
// and a gridline at 71.3% is a number nobody asked about.
const FIT_GRID = 0.05;

// …and never zooms tighter than twenty points. This is the guard on the whole
// idea: a run that moved from 81% to 84% fitted to its own data is a chart of a
// dramatic climb, and the reader has to check the axis to find out it was three
// points. Twenty is wide enough that a genuinely small change looks small.
const MIN_FIT_SPAN = 0.2;

// Gridline spacings worth drawing, coarsest last. The first that divides the
// domain into five intervals or fewer wins — on the full range that is 0.25,
// which is the axis this chart has always had.
const TICK_STEPS = [0.05, 0.1, 0.25, 0.5];

// Every step is at least this many units wide, and the canvas grows until that
// is true. At the fixed 720 units the chart used to be, a sixty-step run gave
// each step 10.7 units — about fourteen screen pixels — so pinning the step you
// meant was a matter of luck, and at a hundred steps it was eight. The plot
// scrolls sideways instead; a run of ten steps is unaffected, because the
// canvas only grows when the arithmetic says it must.
const MIN_STEP_WIDTH = 20;

// The left and bottom gutters carry an axis *title* as well as the tick labels
// now. 38px was exactly wide enough for "100%" and nothing else, so the rotated
// "Accuracy" landed on top of it.
const PAD = { top: 14, right: 14, bottom: 42, left: 54 };

// Which pair of columns the chart is drawing.
//
// A routing run records two accuracies per step and is gated on only one of
// them: whether the agent opened the skills each question was tagged for. The
// judge's verdict on the answers is kept and is worth reading — "the routing is
// fixed and the answers did not improve" is what says to run an isolated pass
// next — but it is not what the run is optimising, and plotting it as *the*
// line would show the gate accepting steps that visibly made things worse.
function metricKeys(metric, mode) {
  const suffix = metric === "soft" ? "soft" : "hard";
  const family = mode === "routing" ? `routing_${suffix}` : suffix;
  return { train: `train_${family}`, val: `val_${family}` };
}

// Migration 0016 is where the routing columns start. A routing run recorded
// before it has them null on every step, and a null draws no point — so a mode
// switch on its own left those runs with two empty lines under an axis reading
// "Routing accuracy", which is the one reading a blank chart must never get:
// nothing on screen separated "not recorded" from "measured, and terrible".
//
// Both halves of the condition are load-bearing. Routing null *throughout* is
// what makes this a recording gap rather than a run whose steps could not be
// scored; a judge column that is actually populated is what makes falling back
// to it worth doing. A run that has only just started is null in both families
// and is neither — it gets its honest empty chart under its own axis.
function routingWasNeverRecorded(steps, metric) {
  const routing = metricKeys(metric, "routing");
  const judge = metricKeys(metric, "isolated");
  let sawJudge = false;
  for (const step of steps || []) {
    if (step[routing.train] != null || step[routing.val] != null) return false;
    if (step[judge.train] != null || step[judge.val] != null) sawJudge = true;
  }
  return sawJudge;
}

/** Which columns to plot, and whether that is the fallback.
 *
 * The fallback is never silent. Plotting the judge's accuracy for these runs
 * without saying so would assert that it is what their gate compared, and it is
 * not — so `legacy` travels out with the keys and ends up in the axis title.
 */
export function plottedMetric(steps, metric = "hard", mode = "isolated") {
  const legacy = mode === "routing" && routingWasNeverRecorded(steps, metric);
  return { keys: metricKeys(metric, legacy ? "isolated" : mode), legacy };
}

/** What the y axis is measuring, in the words the mode makes true. */
export function accuracyLabel(mode, { legacy = false } = {}) {
  if (mode !== "routing") return "Answer accuracy";
  return legacy ? "Answer accuracy (routing not recorded)" : "Routing accuracy";
}

function stateOf(step) {
  if (step.step_no === 0) return "baseline";
  return step.gate_action === "reject" ? "rejected" : "accepted";
}

/** The two series in *step* space: `{train, val}`, x measured in steps.
 *
 * A step with no measurement yet contributes no point at all. The alternative —
 * a zero — draws the skill collapsing to the floor for as long as each rollout
 * takes, which is exactly what a genuinely destroyed skill looks like.
 */
export function series(steps, metric = "hard", { bestStep = null, mode = "isolated" } = {}) {
  const { keys: key } = plottedMetric(steps, metric, mode);
  const train = [];
  const val = [];
  for (const step of steps || []) {
    const trainValue = step[key.train];
    // The baseline buys no training rollout, so it has no train point to plot
    // even once the run is finished.
    if (step.step_no > 0 && trainValue != null) {
      train.push({ stepNo: step.step_no, x: step.step_no - 0.5, value: trainValue, step });
    }
    const valValue = step[key.val];
    if (valValue != null) {
      val.push({
        stepNo: step.step_no,
        x: step.step_no,
        value: valValue,
        state: stateOf(step),
        isBest: bestStep != null && step.step_no === bestStep,
        step,
      });
    }
  }
  return { train, val };
}

/** The gate's threshold as it stood after each step: a staircase that never falls.
 *
 * Read from `best_score`, which the engine writes on the step row after the
 * gate has run — the same number the gate compared against. Deriving it here
 * from the validation series instead would be a second implementation of
 * "which score survived", and the two would eventually disagree on screen about
 * the same run.
 */
export function bestSoFar(steps) {
  const points = [];
  for (const step of steps || []) {
    if (step.best_score == null) continue;
    points.push({ stepNo: step.step_no, x: step.step_no, value: step.best_score });
  }
  return points;
}

/** One band per epoch, in step space, spanning the steps that actually ran.
 *
 * From the first train point of the epoch (`first − 0.5`) to its last
 * validation point (`last + 0.5`), so both of an epoch's series lie inside its
 * own band and consecutive bands meet without a seam. Epoch 0 is the baseline,
 * which is not a pass over the training data and gets no band.
 */
export function epochBands(steps) {
  const bounds = new Map();
  for (const step of steps || []) {
    if (!step.epoch_no) continue;
    const seen = bounds.get(step.epoch_no);
    if (!seen) bounds.set(step.epoch_no, { min: step.step_no, max: step.step_no });
    else {
      seen.min = Math.min(seen.min, step.step_no);
      seen.max = Math.max(seen.max, step.step_no);
    }
  }
  return [...bounds.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([epochNo, { min, max }]) => ({ epochNo, x0: min - 0.5, x1: max + 0.5 }));
}

/** `[min, max]` of the x axis, sized for the whole planned run.
 *
 * Fitting the axis to the steps that have finished would rescale it on every
 * step, sliding every earlier point sideways each time a new one lands — while
 * the run streams, which is when the chart is being watched. `totalSteps` is
 * what was planned; the data still wins if a resumed run overran it.
 */
export function xDomain(steps, totalSteps = 0) {
  const last = Math.max(
    totalSteps || 0,
    ...(steps || []).map((s) => s.step_no),
    0,
  );
  return [-0.5, last + 0.5];
}

// Floating point: 0.72 - 0.05 is 0.6699999999999999, and a domain edge one
// ulp below a gridline puts an extra tick on the axis.
function tidy(value) {
  return Number(value.toFixed(4));
}

function snapDown(value) {
  return tidy(Math.floor(tidy(value / FIT_GRID)) * FIT_GRID);
}

function snapUp(value) {
  return tidy(Math.ceil(tidy(value / FIT_GRID)) * FIT_GRID);
}

/** Every plotted number the axis has to contain, for the series still shown.
 *
 * Hidden series are excluded, and that is the point of passing `show` in here
 * rather than filtering afterwards: turning off the train line on a run whose
 * training scores sat ten points below validation should tighten the axis
 * around what is left, not leave a band of empty plot where the hidden line
 * used to be.
 */
// `runMode` and not `metric` alone: the axis has to be fitted to the column the
// chart draws. Reading the judge's while `series` plotted routing put a run
// whose judge sat at 0.88 and whose routing sat at 0.35 on an axis starting at
// 0.85 — every point below the plot area, on a chart that looked ordinary.
function visibleValues(steps, { show, metric, runMode }) {
  const { keys: key } = plottedMetric(steps, metric, runMode);
  const values = [];
  for (const step of steps || []) {
    if (show.train && step.step_no > 0 && step[key.train] != null) values.push(step[key.train]);
    if (show.val && step[key.val] != null) values.push(step[key.val]);
    if (show.best && step.best_score != null) values.push(step.best_score);
  }
  return values;
}

/** `[min, max]` of the y axis.
 *
 * `full` is 0–100%, which is the honest default for "how good is it" and was
 * the only option this chart had. It is also three quarters of empty plot on
 * every run that works: skills start useful, so the interesting range is the
 * top twenty points and every point in it is drawn within a few pixels of the
 * others.
 *
 * `fit` opens that up, with two rules that keep it from lying. It never zooms
 * tighter than `MIN_FIT_SPAN`, so a three-point wobble cannot fill the plot;
 * and it snaps to five-point marks, so the axis labels stay numbers a reader
 * recognises. The component says "zoomed" on the axis when this is in force —
 * an axis that silently changes meaning is worse than one that never moves.
 */
export function yDomain(steps, options = {}) {
  // `mode` here is the zoom mode, which predates the run mode and keeps the
  // name; `runMode` is the run's, and only picks which column is measured.
  const { mode = "fit", show = ALL_SERIES, metric = "hard", runMode = "isolated" } = options;
  if (mode !== "fit") return [...Y_FULL];

  const values = visibleValues(steps, { show, metric, runMode });
  if (!values.length) return [...Y_FULL];

  let lo = snapDown(Math.min(...values));
  let hi = snapUp(Math.max(...values));

  // A run whose every score is identical snaps to a zero-width domain, which
  // divides by zero in the scale. Widening to the minimum covers that case as
  // well as the merely-too-tight one.
  if (hi - lo < MIN_FIT_SPAN) {
    const middle = (lo + hi) / 2;
    lo = snapDown(middle - MIN_FIT_SPAN / 2);
    hi = snapUp(middle + MIN_FIT_SPAN / 2);
  }

  // Accuracy has no meaning outside 0–100%, so the widened domain slides back
  // inside rather than being clipped — a plot area whose top eighth can never
  // hold a point is the same wasted space this mode exists to remove.
  if (hi > 1) {
    lo = tidy(Math.max(0, lo - (hi - 1)));
    hi = 1;
  }
  if (lo < 0) {
    hi = tidy(Math.min(1, hi - lo));
    lo = 0;
  }
  return [lo, hi];
}

/** The gridlines for a domain: four to six of them, on round numbers. */
export function yTickValues([lo, hi]) {
  const span = hi - lo;
  const stride = TICK_STEPS.find((s) => span / s <= 5) ?? TICK_STEPS.at(-1);
  const ticks = [];
  for (let v = tidy(Math.ceil(tidy(lo / stride)) * stride); v <= hi + 1e-9; v = tidy(v + stride)) {
    ticks.push(v);
  }
  return ticks;
}

function xTickValues(last) {
  if (last <= 0) return [0];
  const stride = Math.max(1, Math.ceil(last / (MAX_X_TICKS - 1)));
  const values = [];
  for (let n = 0; n < last; n += stride) values.push(n);
  // The right-hand end is always drawn: "how far along is it" is read off that
  // label, and a run at step 61 whose axis stops at 60 looks unfinished. When
  // the stride does not divide the run evenly the tick before it would land a
  // pixel away, so that one is dropped rather than left to collide.
  if (last - values.at(-1) < stride / 2) values.pop();
  values.push(last);
  return values;
}

function path(points) {
  return points.map((p, i) => `${i ? "L" : "M"}${round(p.x)},${round(p.y)}`).join(" ");
}

function round(n) {
  return Number(n.toFixed(2));
}

/** Everything the component draws, in pixels.
 *
 * The component is then only markup: it never computes a coordinate, which is
 * what keeps the untestable half of the chart free of arithmetic.
 */
export function chartModel(steps, options = {}) {
  const {
    width: requestedWidth = 640,
    height = 240,
    metric = "hard",
    // Which accuracy this run is gated on, so the plotted line is the one the
    // gate actually compares.
    mode = "isolated",
    bestStep = null,
    totalSteps = 0,
    yMode = "fit",
    show: showOption = ALL_SERIES,
    minStepWidth = MIN_STEP_WIDTH,
  } = options;

  const show = { ...ALL_SERIES, ...showOption };

  const [x0, x1] = xDomain(steps, totalSteps);
  const span = x1 - x0;
  const lastStep = Math.round(x1 - 0.5);

  // The canvas is as wide as the panel, or as wide as the steps need — whichever
  // is more. The caller passes the panel's width; what comes back may be larger,
  // and the component scrolls it. Everything below is in these units, so this
  // has to be settled before a single coordinate is computed.
  const width = Math.max(
    requestedWidth,
    PAD.left + PAD.right + (lastStep + 1) * minStepWidth,
  );

  const plot = {
    left: PAD.left,
    top: PAD.top,
    width: Math.max(1, width - PAD.left - PAD.right),
    height: Math.max(1, height - PAD.top - PAD.bottom),
  };

  const [y0, y1] = yDomain(steps, { mode: yMode, show, metric, runMode: mode });

  const sx = (x) => plot.left + ((x - x0) / span) * plot.width;
  // SVG's y grows downwards; accuracy grows upwards. This is the flip.
  const sy = (v) => plot.top + (1 - (v - y0) / (y1 - y0)) * plot.height;

  const { legacy: legacyMetric } = plottedMetric(steps, metric, mode);
  const { train, val } = series(steps, metric, { bestStep, mode });
  // A hidden series is dropped here rather than in the component, so the paths,
  // the markers and the axis it was fitted to can never disagree about which
  // lines are on screen.
  const trainPx = show.train ? train.map((p) => ({ ...p, x: sx(p.x), y: sy(p.value) })) : [];
  const valPx = show.val ? val.map((p) => ({ ...p, x: sx(p.x), y: sy(p.value) })) : [];

  // The staircase: carry the old threshold across to the new step, then rise.
  const best = show.best ? bestSoFar(steps) : [];
  const bestPoints = [];
  best.forEach((p, i) => {
    if (i) bestPoints.push({ x: sx(p.x), y: sy(best[i - 1].value) });
    bestPoints.push({ x: sx(p.x), y: sy(p.value) });
  });

  const bands = epochBands(steps).map((b) => ({
    epochNo: b.epochNo,
    x: sx(b.x0),
    width: Math.max(0, sx(b.x1) - sx(b.x0)),
  }));

  // One full-height band per step, spanning the half-step either side of it.
  //
  // These are what makes a step look clickable and look picked. Before them the
  // only pinned-state styling was a stroke on the validation dot, which meant a
  // step whose candidate the gate rejected — drawn as a cross, with no circle in
  // it — showed *nothing at all* when you clicked it, and a step whose
  // validation was skipped entirely had no marker to style in the first place.
  // Both are common: a run where every candidate is rejected is a run where
  // clicking the chart appeared to do nothing.
  //
  // A band is independent of whether the step has a point, so the feedback is
  // there whether or not the step managed to produce one.
  const columns = [];
  for (let stepNo = 0; stepNo <= lastStep; stepNo += 1) {
    const left = sx(stepNo - 0.5);
    columns.push({
      stepNo,
      x: left,
      width: Math.max(0, sx(stepNo + 0.5) - left),
      cx: sx(stepNo),
    });
  }

  return {
    // The canvas the component must draw at, which is not necessarily the width
    // it asked for — see `minStepWidth`. It is also the number the component
    // needs to turn a pointer position into a coordinate, and getting that from
    // a constant instead is how a scrolled chart reports the wrong step.
    width,
    height,
    plot,
    metric,
    show,
    columns,
    yDomain: [y0, y1],
    // Whether the axis is showing something other than the full range, so the
    // component can say so. A zoomed axis that does not announce itself is the
    // one way this feature could mislead.
    zoomed: y0 !== Y_FULL[0] || y1 !== Y_FULL[1],
    // Whether the plotted column is the fallback of `plottedMetric` — a routing
    // run from before the routing columns existed, drawn with the judge's
    // numbers. The component puts this in the axis title, because a chart that
    // substitutes one measurement for another without saying so is the failure
    // this fallback was added to remove, not a smaller version of it.
    legacyMetric,
    train: trainPx,
    val: valPx,
    trainPath: path(trainPx),
    valPath: path(valPx),
    bestPath: path(bestPoints),
    bands,
    yTicks: yTickValues([y0, y1]).map((value) => ({
      value,
      y: sy(value),
      label: `${Math.round(value * 100)}%`,
    })),
    xTicks: xTickValues(lastStep).map((stepNo) => ({
      stepNo,
      x: sx(stepNo),
      // Step 0 is the initial skill, not a step of training. A developer
      // hunting for the starting score does not look for it under "0".
      label: stepNo === 0 ? "base" : String(stepNo),
    })),
    /** The step nearest a pixel x — the click target is the column, not the dot. */
    stepAt(px) {
      const value = x0 + ((px - plot.left) / plot.width) * span;
      return Math.min(lastStep, Math.max(0, Math.round(value)));
    },
    /**
     * The step under a point, or null outside the plot area.
     *
     * `stepAt` clamps, which is right inside the plot — the column is the
     * target, not the dot — and wrong everywhere else. The svg element is the
     * full 720×260 including a 38px left gutter of axis labels and a 26px strip
     * of step numbers below, so a click anywhere on those pinned step 0 or the
     * last step, and moving the pointer across the y-axis labels showed a
     * readout for a step nowhere near it.
     */
    stepAtPoint(px, py) {
      if (px < plot.left || px > plot.left + plot.width) return null;
      if (py < plot.top || py > plot.top + plot.height) return null;
      const value = x0 + ((px - plot.left) / plot.width) * span;
      return Math.min(lastStep, Math.max(0, Math.round(value)));
    },
  };
}
