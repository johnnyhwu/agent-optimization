import React, { useMemo, useRef, useState } from "react";
import { chartModel } from "../../optimize_chart.js";

// The run, as a picture. Hand-written SVG: this repo has no chart library, no
// router and no state library, and a dependency whose whole job is to draw
// twenty circles and three lines would be the largest thing in the bundle.
//
// Every coordinate comes from `optimize_chart.js`, which is tested. This file
// is markup — it must stay that way, because nothing in it can be tested at
// all (`node --test` loads pure modules only).
//
// Three things this chart has to do, none of which it used to:
//
//   * **Say what the axes are.** They were an unlabelled 0–100% and an
//     unlabelled run of step numbers, so the first question a new reader asked
//     was what either of them meant.
//   * **Look clickable.** The affordance was a sentence underneath saying
//     "Click a step to pin its summary below", which is what an interface says
//     when its shapes do not. Now the step under the pointer lights its whole
//     column and its axis label, which is both the invitation and the target.
//   * **Look picked.** Pinning styled the validation dot — and a rejected
//     candidate is drawn as a cross with no dot, so on a run where the gate
//     rejected everything, clicking did nothing visible at all. The pin is now
//     the column plus a marker line, which exist for every step.
//
// Hover shows numbers in a tooltip that floats over the plot rather than in a
// line of text underneath: as a block in the flow it pushed the pinned card
// down the page every time the pointer crossed the chart. Click pins the card,
// which is where the buttons live — buttons inside a hover card are a known
// trap, the pointer has to cross a gap to reach them and a keyboard never can.

const WIDTH = 720;
const HEIGHT = 280;

export default function ProgressChart({ steps, totalSteps, bestStep, metric, onPick, pinned }) {
  const svgRef = useRef(null);
  const [hover, setHover] = useState(null);

  const model = useMemo(
    () => chartModel(steps, { width: WIDTH, height: HEIGHT, totalSteps, bestStep, metric }),
    [steps, totalSteps, bestStep, metric],
  );

  if (!steps.length) {
    return (
      <p className="opt-hint">
        The chart appears once the baseline has been measured.
      </p>
    );
  }

  // The pointer is in CSS pixels of a scaled SVG; the model is in viewBox
  // units. Without the ratio, every reading is wrong by the scale factor —
  // subtly on a wide panel, wildly on a narrow one.
  //
  // Both axes, because the plot is inset from the canvas on all four sides and
  // the model has to be asked about a point rather than a column.
  function toViewBox(event) {
    const box = svgRef.current.getBoundingClientRect();
    return [
      ((event.clientX - box.left) / box.width) * WIDTH,
      ((event.clientY - box.top) / box.height) * HEIGHT,
    ];
  }

  const hovered = hover != null ? steps.find((s) => s.step_no === hover) : null;
  const hoverColumn = hover != null ? model.columns.find((c) => c.stepNo === hover) : null;
  const pinnedColumn = pinned != null ? model.columns.find((c) => c.stepNo === pinned) : null;

  return (
    <div className="opt-chart">
      <div className="opt-chart-frame">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          className="opt-chart-svg"
          role="img"
          aria-label={
            `Accuracy by step. ${steps.length} steps measured` +
            (bestStep != null ? `, best at step ${bestStep}.` : ".") +
            " The table below carries the same numbers."
          }
          onMouseMove={(e) => setHover(model.stepAtPoint(...toViewBox(e)))}
          onMouseLeave={() => setHover(null)}
          // Outside the plot is not a step. A click on the axis labels used to
          // pin whichever step the clamp landed on, which is a pin the user did
          // not ask for on a step they were not pointing at.
          onClick={(e) => {
            const stepNo = model.stepAtPoint(...toViewBox(e));
            if (stepNo != null) onPick(stepNo);
          }}
        >
          {/* Epoch bands first: everything else draws on top of them. */}
          {model.bands.map((band, index) => (
            <rect
              key={band.epochNo}
              x={band.x}
              y={model.plot.top}
              width={band.width}
              height={model.plot.height}
              className={index % 2 ? "opt-chart-band alt" : "opt-chart-band"}
            />
          ))}
          {model.bands.map((band) => (
            <text
              key={`label-${band.epochNo}`}
              x={band.x + band.width / 2}
              y={model.plot.top + 11}
              className="opt-chart-epoch"
              textAnchor="middle"
            >
              epoch {band.epochNo}
            </text>
          ))}

          {/* The picked column, under the data so the series stay readable
              through it. Drawn from the column band rather than from a marker,
              which is the whole point: a rejected step's cross and a skipped
              step's absent dot both leave nothing to highlight. */}
          {pinnedColumn && (
            <rect
              x={pinnedColumn.x}
              y={model.plot.top}
              width={pinnedColumn.width}
              height={model.plot.height}
              className="opt-chart-col pinned"
            />
          )}
          {hoverColumn && hoverColumn.stepNo !== pinned && (
            <rect
              x={hoverColumn.x}
              y={model.plot.top}
              width={hoverColumn.width}
              height={model.plot.height}
              className="opt-chart-col hover"
            />
          )}

          {model.yTicks.map((tick) => (
            <g key={tick.value}>
              <line
                x1={model.plot.left}
                x2={model.plot.left + model.plot.width}
                y1={tick.y}
                y2={tick.y}
                className="opt-chart-grid"
              />
              <text x={model.plot.left - 6} y={tick.y + 3} className="opt-chart-tick" textAnchor="end">
                {tick.label}
              </text>
            </g>
          ))}

          {/* Axis titles. Without them the y axis is an unexplained 0–100% and
              the x axis an unexplained run of integers, which is most of what
              made this chart unreadable to anyone meeting it for the first
              time. */}
          <text
            className="opt-chart-axis"
            textAnchor="middle"
            transform={`translate(13, ${model.plot.top + model.plot.height / 2}) rotate(-90)`}
          >
            Accuracy
          </text>
          <text
            className="opt-chart-axis"
            x={model.plot.left + model.plot.width / 2}
            y={HEIGHT - 6}
            textAnchor="middle"
          >
            Step
          </text>

          {/* The pin's own line, so the picked step is unmistakable even where
              two columns are only a few pixels wide. */}
          {pinnedColumn && (
            <line
              x1={pinnedColumn.cx}
              x2={pinnedColumn.cx}
              y1={model.plot.top}
              y2={model.plot.top + model.plot.height}
              className="opt-chart-pinline"
            />
          )}

          {model.xTicks.map((tick) => {
            const state =
              tick.stepNo === pinned ? " pinned" : tick.stepNo === hover ? " hover" : "";
            return (
              <text
                key={tick.stepNo}
                x={tick.x}
                y={HEIGHT - 24}
                className={`opt-chart-tick step${state}`}
                textAnchor="middle"
              >
                {tick.label}
              </text>
            );
          })}

          {/* The gate's own threshold. Drawn under the series it judges. */}
          <path d={model.bestPath} className="opt-chart-best" />
          <path d={model.trainPath} className="opt-chart-line train" />
          <path d={model.valPath} className="opt-chart-line val" />

          {model.train.map((p) => (
            <circle key={p.stepNo} cx={p.x} cy={p.y} r={3} className="opt-chart-dot train" />
          ))}
          {model.val.map((p) => (
            <ValMarker key={p.stepNo} point={p} pinned={pinned === p.stepNo} />
          ))}
        </svg>

        {/* Absolutely positioned over the plot, so it can appear and disappear
            without moving a single pixel of the page underneath it. */}
        {hovered && hoverColumn && (
          <HoverTip step={hovered} metric={metric} xPercent={(hoverColumn.cx / WIDTH) * 100} />
        )}
      </div>

      <div className="opt-chart-legend">
        <span><i className="swatch train" /> train (before the edit)</span>
        <span><i className="swatch val" /> validation (after)</span>
        <span><i className="swatch best" /> best so far — the gate's threshold</span>
        <span><i className="swatch rejected" /> rejected</span>
      </div>
    </div>
  );
}

function ValMarker({ point, pinned }) {
  const classes = ["opt-chart-dot", "val", point.state];
  if (point.isBest) classes.push("best");
  if (pinned) classes.push("pinned");
  // A rejected candidate is a cross, not a hollow circle: the two shapes stay
  // distinguishable when the line passes through them and when the chart is
  // printed in grey.
  if (point.state === "rejected") {
    const r = 4;
    return (
      <g className={classes.join(" ")}>
        <line x1={point.x - r} y1={point.y - r} x2={point.x + r} y2={point.y + r} />
        <line x1={point.x - r} y1={point.y + r} x2={point.x + r} y2={point.y - r} />
      </g>
    );
  }
  return (
    <g className={classes.join(" ")}>
      {point.isBest && <circle cx={point.x} cy={point.y} r={7} className="opt-chart-ring" />}
      <circle cx={point.x} cy={point.y} r={4} />
    </g>
  );
}

// Three numbers and a verdict — enough to compare two steps without clicking
// either. Anything actionable, and anything that needs reading rather than
// glancing at, belongs on the pinned card.
function HoverTip({ step, metric, xPercent }) {
  const suffix = metric === "soft" ? "soft" : "hard";
  // Clamped so a tooltip on the first or last step does not hang off the panel.
  const left = Math.min(88, Math.max(12, xPercent));
  return (
    <div className="opt-chart-tip" style={{ left: `${left}%` }} aria-hidden="true">
      <strong>{step.step_no === 0 ? "Baseline" : `Step ${step.step_no}`}</strong>
      <span><i className="swatch train" /> train {pct(step[`train_${suffix}`])}</span>
      <span><i className="swatch val" /> validation {pct(step[`val_${suffix}`])}</span>
      {step.gate_action && (
        <span className="verdict">
          {step.gate_action === "reject" ? "rejected" : step.gate_action.replace(/_/g, " ")}
        </span>
      )}
    </div>
  );
}

function pct(value) {
  return value == null ? "—" : `${Math.round(value * 100)}%`;
}
