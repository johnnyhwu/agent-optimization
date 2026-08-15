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

export const Y_DOMAIN = [0, 1];

const Y_TICK_VALUES = [0, 0.25, 0.5, 0.75, 1];

// Enough ticks to locate a step, few enough to stay legible at panel width.
const MAX_X_TICKS = 16;

// The left and bottom gutters carry an axis *title* as well as the tick labels
// now. 38px was exactly wide enough for "100%" and nothing else, so the rotated
// "Accuracy" landed on top of it.
const PAD = { top: 14, right: 14, bottom: 42, left: 54 };

function metricKeys(metric) {
  const suffix = metric === "soft" ? "soft" : "hard";
  return { train: `train_${suffix}`, val: `val_${suffix}` };
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
export function series(steps, metric = "hard", { bestStep = null } = {}) {
  const key = metricKeys(metric);
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
    width = 640,
    height = 240,
    metric = "hard",
    bestStep = null,
    totalSteps = 0,
  } = options;

  const plot = {
    left: PAD.left,
    top: PAD.top,
    width: Math.max(1, width - PAD.left - PAD.right),
    height: Math.max(1, height - PAD.top - PAD.bottom),
  };

  const [x0, x1] = xDomain(steps, totalSteps);
  const span = x1 - x0;
  const sx = (x) => plot.left + ((x - x0) / span) * plot.width;
  // SVG's y grows downwards; accuracy grows upwards. This is the flip.
  const sy = (v) => plot.top + (1 - (v - Y_DOMAIN[0]) / (Y_DOMAIN[1] - Y_DOMAIN[0])) * plot.height;

  const { train, val } = series(steps, metric, { bestStep });
  const trainPx = train.map((p) => ({ ...p, x: sx(p.x), y: sy(p.value) }));
  const valPx = val.map((p) => ({ ...p, x: sx(p.x), y: sy(p.value) }));

  // The staircase: carry the old threshold across to the new step, then rise.
  const best = bestSoFar(steps);
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

  const lastStep = Math.round(x1 - 0.5);
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
    plot,
    metric,
    columns,
    train: trainPx,
    val: valPx,
    trainPath: path(trainPx),
    valPath: path(valPx),
    bestPath: path(bestPoints),
    bands,
    yTicks: Y_TICK_VALUES.map((value) => ({ value, y: sy(value), label: `${value * 100}%` })),
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
