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
// Hover shows numbers. Click pins a card, which is where the buttons live.
// Buttons inside a hover card are a known trap: the pointer has to cross a gap
// to reach them, and a keyboard never can.

const WIDTH = 720;
const HEIGHT = 260;

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
  function toViewBoxX(event) {
    const box = svgRef.current.getBoundingClientRect();
    return ((event.clientX - box.left) / box.width) * WIDTH;
  }

  const hovered = hover != null ? steps.find((s) => s.step_no === hover) : null;

  return (
    <div className="opt-chart">
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
        onMouseMove={(e) => setHover(model.stepAt(toViewBoxX(e)))}
        onMouseLeave={() => setHover(null)}
        onClick={(e) => onPick(model.stepAt(toViewBoxX(e)))}
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
        {model.xTicks.map((tick) => (
          <text
            key={tick.stepNo}
            x={tick.x}
            y={HEIGHT - 8}
            className="opt-chart-tick"
            textAnchor="middle"
          >
            {tick.label}
          </text>
        ))}

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

      <div className="opt-chart-legend">
        <span><i className="swatch train" /> train (before the edit)</span>
        <span><i className="swatch val" /> validation (after)</span>
        <span><i className="swatch best" /> best so far — the gate's threshold</span>
        <span><i className="swatch rejected" /> rejected</span>
      </div>

      {hovered && <HoverReadout step={hovered} metric={metric} />}
      <p className="opt-hint">Click a step to pin its summary below.</p>
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

// Numbers only. Anything actionable belongs on the pinned card.
function HoverReadout({ step, metric }) {
  const suffix = metric === "soft" ? "soft" : "hard";
  return (
    <div className="opt-chart-readout">
      <strong>{step.step_no === 0 ? "baseline" : `step ${step.step_no}`}</strong>
      <span>train {pct(step[`train_${suffix}`])}</span>
      <span>validation {pct(step[`val_${suffix}`])}</span>
      {step.gate_action && (
        <span>
          {step.gate_action === "reject"
            ? `rejected (${step.gate_reject_reason})`
            : step.gate_action.replace(/_/g, " ")}
        </span>
      )}
    </div>
  );
}

function pct(value) {
  return value == null ? "—" : `${Math.round(value * 100)}%`;
}
