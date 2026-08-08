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
//
// **`running` is load-bearing, not a convenience.** A measured duration only
// exists once the agent has answered: a timeout, a transport error or a stop
// press all leave the row with a start time and no duration. Deciding "still
// running" from the absence of a duration would therefore leave a dead row
// counting up for as long as the page stayed open — the exact never-settles
// symptom this whole change set exists to remove. The caller knows whether the
// work is still happening, so it says so.

const RUNNING_HINT =
  "The agent has been working on this question for %s. Timing stops when it " +
  "answers; grading and trace analysis are not counted.";
const SETTLED_HINT =
  "The agent took %s to answer. Grading and trace analysis are not counted.";

function Timer({ startedAt }) {
  // Subscribed unconditionally, because hooks cannot be conditional — which is
  // why every other case returns before reaching this component at all. A list
  // with nothing in flight therefore holds no subscribers, and the ticker stops.
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

export default function ElapsedTimer({ startedAt, finalMs, running = false }) {
  // Settled: the server measured this, so show it whatever the row's state —
  // an agent that answered and then failed still took the time it took.
  if (finalMs !== null && finalMs !== undefined) {
    const text = formatDuration(finalMs);
    return (
      <span className="elapsed" title={SETTLED_HINT.replace("%s", text)}>
        {text}
      </span>
    );
  }
  // Nothing truthful to show, so nothing is shown. Three cases land here, and
  // silence is the honest answer to all of them: the question has not started;
  // the row predates the start time being recorded; or the work ended without
  // the agent ever answering, so how long it *would* have taken is not a
  // question this platform can answer.
  if (!running || !startedAt) return null;
  return <Timer startedAt={startedAt} />;
}
