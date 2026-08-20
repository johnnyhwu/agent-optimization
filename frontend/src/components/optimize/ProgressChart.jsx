import React, { useEffect, useMemo, useRef, useState } from "react";
import Badge from "../ui/Badge.jsx";
import { chartModel } from "../../optimize_chart.js";
import { gateLabel } from "../../optimize_gate_label.js";

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
// Hover writes into a fixed-height readout *above* the plot, and this is the
// third arrangement it has had. In the flow underneath, it pushed the pinned
// card down the page every time the pointer crossed the chart. Floating over
// the plot, it stopped moving the page and started covering the data: it was
// pinned to the top of the canvas, which on any run worth reading is exactly
// where the points are. A reserved row above the plot moves nothing, covers
// nothing, and — being panel-wide rather than a small box — has room to say
// what the gate actually decided instead of printing "reject". The keyboard
// gets a readout for the first time as well; arrow keys pin a step, and a
// pointer-only tooltip never showed them anything.
//
// Click still pins the card below, which is where the buttons live — buttons
// inside a hover card are a known trap, the pointer has to cross a gap to reach
// them and a keyboard never can.
//
// And three that were the reason a long run was hard to read at all:
//
//   * **The canvas is as wide as the run needs.** At a fixed 720 units a
//     sixty-step run gave each step fourteen screen pixels to click and a
//     hundred-step run eight. The model decides the width; the frame scrolls.
//   * **The keyboard can pin a step.** Arrow keys walk the columns and follow
//     the scroll. Clicking a point on an SVG is a pointer-only gesture, and the
//     step table below was the only way in.
//   * **The legend switches series off.** Which is also how the fitted axis
//     stops being dominated by a series the reader is not asking about.

// The canvas, in CSS pixels, and it stays that tall whatever the run does.
//
// It did not used to. The svg carried a `width` attribute and no `height`, with
// `min-width: 100%; height: auto` in the stylesheet, so a short run's canvas
// was stretched to the panel and its height scaled up with it — a 720-unit
// chart in a 1100px panel rendered 428px tall, text and dots and all. A long
// run's canvas is wider than the panel, nothing stretches, and it rendered at
// its native height. The two states differed by half again, and because the
// second is the one with the scrollbar, the scrollbar looked like the cause.
//
// Both attributes are set now and the model is asked for the panel's measured
// width, so one viewBox unit is one CSS pixel in every case.
const HEIGHT = 320;
// Until the frame has been measured. Any positive number does; this one is what
// the chart asked for before it could measure anything.
const FALLBACK_WIDTH = 720;

const SERIES = [
  { key: "train", label: "train (before the edit)" },
  { key: "val", label: "validation (after)" },
  { key: "best", label: "best so far — the gate's threshold" },
];

export default function ProgressChart({
  steps, totalSteps, bestStep, metric, onPick, pinned,
  yMode = "fit", show, onToggleSeries,
}) {
  const svgRef = useRef(null);
  const frameRef = useRef(null);
  const [hover, setHover] = useState(null);
  const [panelWidth, setPanelWidth] = useState(FALLBACK_WIDTH);

  // The width the plot actually has. Only the width: the height is fixed, so
  // observing both would let a re-render that changes the canvas feed back into
  // the observer that caused it.
  useEffect(() => {
    const frame = frameRef.current;
    if (!frame || typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(([entry]) => {
      const width = Math.round(entry.contentRect.width);
      if (width > 0) setPanelWidth(width);
    });
    observer.observe(frame);
    return () => observer.disconnect();
  }, []);

  const model = useMemo(
    () => chartModel(steps, {
      width: panelWidth, height: HEIGHT, totalSteps, bestStep, metric, yMode, show,
    }),
    [steps, panelWidth, totalSteps, bestStep, metric, yMode, show],
  );

  // Keep the pinned step in view when it moves under the keyboard. Only when it
  // is off screen — `nearest` scrolls the minimum — so pinning something already
  // visible does not jump the plot sideways under the reader.
  useEffect(() => {
    if (pinned == null || !frameRef.current) return;
    const column = frameRef.current.querySelector(`[data-step="${pinned}"]`);
    column?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [pinned]);

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
  // the model has to be asked about a point rather than a column. `model.width`
  // rather than a constant: on a long run the canvas is wider than the panel
  // asked for, and mapping through the old number would land the reading a
  // handful of steps away from the pointer.
  function toViewBox(event) {
    const box = svgRef.current.getBoundingClientRect();
    return [
      ((event.clientX - box.left) / box.width) * model.width,
      ((event.clientY - box.top) / box.height) * HEIGHT,
    ];
  }

  const lastStep = model.columns.at(-1)?.stepNo ?? 0;

  // Arrow keys walk the columns rather than the plotted points: a step whose
  // candidate was rejected or whose validation was skipped has no marker, and
  // skipping over those would make the keyboard disagree with the pointer about
  // what is on the chart.
  function onKeyDown(event) {
    // Always a set, never a toggle: walking off the end of the axis with the
    // arrow key would otherwise unpin the step it stopped on.
    const move = (to) => {
      event.preventDefault();
      onPick(Math.min(lastStep, Math.max(0, to)));
    };
    if (event.key === "ArrowRight") return move(pinned == null ? 0 : pinned + 1);
    if (event.key === "ArrowLeft") return move(pinned == null ? lastStep : pinned - 1);
    if (event.key === "Home") return move(0);
    if (event.key === "End") return move(lastStep);
    if (event.key === "Escape" && pinned != null) {
      event.preventDefault();
      onPick(null);
    }
  }

  const hovered = hover != null ? steps.find((s) => s.step_no === hover) : null;
  const hoverColumn = hover != null ? model.columns.find((c) => c.stepNo === hover) : null;
  const pinnedColumn = pinned != null ? model.columns.find((c) => c.stepNo === pinned) : null;

  // What the readout is describing: the step under the pointer, else the pinned
  // one, else the newest. Never nothing — the row is a fixed height whether or
  // not the pointer is on the chart, so leaving it blank would be a band of
  // empty space rather than a smaller page.
  const readoutStep = hovered || (pinned != null ? steps.find((s) => s.step_no === pinned) : null)
    || steps.at(-1);

  return (
    <div className="opt-chart">
      <Readout
        step={readoutStep}
        metric={metric}
        show={model.show}
        source={hovered ? "hover" : pinned != null ? "pinned" : "latest"}
      />
      {/* Scrolls when the model asked for more width than the panel has: the
          canvas grows so that every step stays big enough to click, and the
          frame is what slides. Its height never changes — see `HEIGHT`. */}
      <div className="opt-chart-frame" ref={frameRef}>
        <svg
          ref={svgRef}
          viewBox={`0 0 ${model.width} ${HEIGHT}`}
          width={model.width}
          height={HEIGHT}
          className="opt-chart-svg"
          role="img"
          tabIndex={0}
          aria-label={
            `Accuracy by step. ${steps.length} steps measured` +
            (bestStep != null ? `, best at step ${bestStep}.` : ".") +
            " Use the left and right arrow keys to pin a step." +
            " The table below carries the same numbers."
          }
          onKeyDown={onKeyDown}
          onMouseMove={(e) => setHover(model.stepAtPoint(...toViewBox(e)))}
          onMouseLeave={() => setHover(null)}
          // Outside the plot is not a step. A click on the axis labels used to
          // pin whichever step the clamp landed on, which is a pin the user did
          // not ask for on a step they were not pointing at.
          // `onPick` is handed the new pinned step, not a step to toggle: the
          // keyboard needs to set one without the risk of clearing it, so the
          // one place that knows both what was clicked and what is pinned —
          // here — decides which it is.
          onClick={(e) => {
            const stepNo = model.stepAtPoint(...toViewBox(e));
            if (stepNo != null) onPick(stepNo === pinned ? null : stepNo);
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
              time. The zoomed note is not decoration: an axis that quietly
              stopped starting at zero is the one way this chart could mislead,
              so it says so where the axis is named. */}
          <text
            className="opt-chart-axis"
            textAnchor="middle"
            transform={`translate(13, ${model.plot.top + model.plot.height / 2}) rotate(-90)`}
          >
            Accuracy{model.zoomed ? " (zoomed)" : ""}
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

          {/* Nothing is drawn for these — they are what the keyboard scrolls
              into view, and a zero-opacity rect is the only thing in the plot
              that knows where a step is in the scroll container. */}
          {model.columns.map((column) => (
            <rect
              key={`anchor-${column.stepNo}`}
              data-step={column.stepNo}
              x={column.x}
              y={model.plot.top}
              width={column.width}
              height={model.plot.height}
              className="opt-chart-anchor"
            />
          ))}

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

          {/* The pointer's own line, over everything. The column highlight
              alone is ambiguous once the columns are only a few pixels wide,
              and this is the thing that ties the row of numbers above the plot
              to the place in it they describe. */}
          {hoverColumn && (
            <line
              x1={hoverColumn.cx}
              x2={hoverColumn.cx}
              y1={model.plot.top}
              y2={model.plot.top + model.plot.height}
              className="opt-chart-crosshair"
            />
          )}
        </svg>
      </div>

      {/* The legend is the switch. Three lines over twenty steps is a thicket,
          and the question in front of the reader is usually about one of them —
          did validation hold up, did the gate ever move. Buttons rather than a
          control elsewhere on the page, because the legend is already the list
          of what is drawn and where the eye goes to ask what a colour means. */}
      <div className="opt-chart-legend">
        {SERIES.map(({ key, label }) => (
          <button
            key={key}
            type="button"
            className={`opt-chart-legend-item${model.show[key] ? "" : " is-off"}`}
            aria-pressed={model.show[key]}
            onClick={() => onToggleSeries(key)}
            title={model.show[key] ? `Hide ${label}` : `Show ${label}`}
          >
            <i className={`swatch ${key}`} /> {label}
          </button>
        ))}
        <span className="opt-chart-legend-note"><i className="swatch rejected" /> rejected</span>
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

// The readout: which step, what it scored, and what the gate did about it.
//
// A fixed-height row above the plot rather than a box floating over it. The box
// was pinned to the top of the canvas, which is where the points are on any run
// that works, so reading a number meant covering the two beside it. It was also
// about twelve words wide, which is why the verdict in it read "reject" — and
// "reject" is the one word on this page a reader has to act on. There is room
// here to say which guard refused the candidate, or that no guard did and the
// agent server simply stopped answering.
function Readout({ step, metric, show, source }) {
  const suffix = metric === "soft" ? "soft" : "hard";
  const verdict = gateLabel(step);
  return (
    <div className="opt-chart-readout" aria-live="off">
      <strong className="opt-chart-readout-step">
        {step.step_no === 0 ? "Baseline" : `Step ${step.step_no}`}
      </strong>
      {show.train && (
        <span className="opt-chart-readout-score">
          <i className="swatch train" /> train {pct(step[`train_${suffix}`])}
        </span>
      )}
      {show.val && (
        <span className="opt-chart-readout-score">
          <i className="swatch val" /> validation {pct(step[`val_${suffix}`])}
        </span>
      )}
      <Badge tone={verdict.tone} size="sm">{verdict.short}</Badge>
      <span className="opt-chart-readout-detail">{verdict.detail}</span>
      {/* Which step this is, when it is not the one under the pointer. Without
          it the row looks like a stale hover rather than a standing summary. */}
      {source !== "hover" && (
        <span className="opt-chart-readout-source">
          {source === "pinned" ? "pinned" : "latest"}
        </span>
      )}
    </div>
  );
}

function pct(value) {
  return value == null ? "—" : `${Math.round(value * 100)}%`;
}
