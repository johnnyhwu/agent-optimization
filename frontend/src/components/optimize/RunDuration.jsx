import React from "react";
import { durationLabel } from "../../optimize_duration.js";
import { useTick } from "../../useElapsed.js";

// How long this run has taken, under the moment it started.
//
// **Small on purpose**, exactly as `ElapsedTimer` is: while the run is going,
// this is the only thing on the page subscribed to the per-second tick, so a
// tick repaints these few words and leaves the header's facts, the chart and
// the step table alone. Folding it into `RunHeader` would redraw a chart of
// sixty points once a second to change two characters.
//
// Which sentence to show is `durationLabel` in ../../optimize_duration.js —
// pure, so a run interrupted by a restart and a run resumed three days later
// are covered by tests rather than by reading the header on a bad afternoon.

// The whole subtitle, not just the number: a run whose duration cannot be
// stated — one interrupted before its first step finished — must leave "by
// alice" alone rather than a dangling separator in front of it. Composing it
// here keeps that case beside the rule that produces it.
//
// `label.title` is dropped from the markup: it explained what the span measured
// on hover, and the element it was on is now one of two lines rather than the
// number itself. The explanation lives in the run header beside the number it
// is about.
function Label({ run, steps, by }) {
  const label = durationLabel(run, steps);
  // Two lines, always — including the empty one when a run has no duration to
  // report yet. Both halves used to share a line, which meant the fixed grid
  // cell had to fit "running for 1h 01m · by alice" and could not: it wrapped,
  // and the facts row grew by a line every time the number got wider.
  //
  // Stopping the wrap alone would have truncated "by alice" instead — measured
  // at 520, 620 and 760px, where the one-line version ends in an ellipsis. So
  // the line break is deliberate rather than left to the browser, and the height
  // is then the same whatever the number says.
  return (
    <>
      <span className="opt-rundur">{label ? label.text : ""}</span>
      <span className="opt-rundur">by {by}</span>
    </>
  );
}

// Split out because hooks cannot be conditional: only a live run subscribes, so
// a page showing a finished run holds no subscribers and the ticker stops.
function LiveDuration(props) {
  useTick();
  return <Label {...props} />;
}

export default function RunDuration({ run, steps, by }) {
  const running = run.status === "running" || run.status === "pending";
  if (running) return <LiveDuration run={run} steps={steps} by={by} />;
  return <Label run={run} steps={steps} by={by} />;
}
