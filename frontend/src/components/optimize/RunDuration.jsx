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
function Label({ run, steps, by }) {
  const label = durationLabel(run, steps);
  if (!label) return <>by {by}</>;
  return (
    <>
      <span title={label.title}>{label.text}</span>
      {" · "}by {by}
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
