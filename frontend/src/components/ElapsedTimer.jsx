import React from "react";
import { elapsedSince, formatDuration, useTick } from "../useElapsed.js";

// How long a question has been with the agent, or how long it took.
//
// **This component is small on purpose.** It is the only thing subscribed to the
// per-second tick, so a tick re-renders this and nothing else — the row around
// it, with its badges, tag chips and buttons, is untouched. Folding the timer
// into the row would repaint the whole list once a second to change two
// characters, which is the difference between a screen that feels alive and one
// that feels heavy.
//
// The number means **the agent's time**: it starts when the question is sent and
// stops the moment the agent answers. Judging, trace ingestion and diagnosis all
// happen afterwards and are deliberately excluded — they are this platform's
// latency, not the agent's, and mixing them in would make the one number anyone
// wants to compare between attempts incomparable. That is also why a settled
// timer can sit next to a row still reading "judging…", and why both the tooltip
// and the list footers say so in words rather than leaving it to be inferred.

const RUNNING_HINT =
  "The agent has been working on this question for %s. Timing stops when it " +
  "answers; grading and trace analysis are not counted.";
const SETTLED_HINT =
  "The agent took %s to answer. Grading and trace analysis are not counted.";

function Timer({ startedAt, finalMs }) {
  // Subscribed unconditionally, because hooks cannot be conditional — which is
  // why the settled and unknown cases return before reaching this component at
  // all. A finished list therefore holds no subscribers, and the ticker stops.
  useTick();

  const elapsed = elapsedSince(startedAt);
  if (elapsed === null) return null;
  // Whole seconds while running: a flickering tenth on a number that is still
  // moving is noise, and it is the settled value that deserves the precision.
  const text = formatDuration(Math.floor(elapsed / 1000) * 1000);
  return (
    <span className="elapsed is-running" title={RUNNING_HINT.replace("%s", text)}>
      {text}
    </span>
  );
}

export default function ElapsedTimer({ startedAt, finalMs }) {
  // Settled: render the authoritative duration the server measured, and take no
  // part in the ticking.
  if (finalMs !== null && finalMs !== undefined) {
    const text = formatDuration(finalMs);
    return (
      <span className="elapsed" title={SETTLED_HINT.replace("%s", text)}>
        {text}
      </span>
    );
  }
  // Not started, or a row from before the start time was recorded. Nothing
  // truthful to show, so nothing is shown — better than a 0s that looks stuck.
  if (!startedAt) return null;
  return <Timer startedAt={startedAt} finalMs={finalMs} />;
}
