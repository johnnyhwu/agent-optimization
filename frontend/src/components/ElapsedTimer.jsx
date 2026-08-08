import React from "react";
import { formatDuration, timerState, useTick } from "../useElapsed.js";

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
// Which of the three things to show is `timerState` in ../useElapsed.js — pure,
// so that decision is covered by unit tests rather than by driving a browser.

const RUNNING_HINT =
  "The agent has been working on this question for %s. Timing stops when it " +
  "answers; grading and trace analysis are not counted.";
const SETTLED_HINT =
  "The agent took %s to answer. Grading and trace analysis are not counted.";

// Split out because hooks cannot be conditional: only this component subscribes
// to the tick, so a list with nothing in flight holds no subscribers at all and
// the ticker stops.
function RunningTimer({ startedAt }) {
  useTick();
  const { kind, ms } = timerState({ startedAt, running: true });
  if (kind !== "running") return null;
  const text = formatDuration(ms);
  return (
    <span className="elapsed is-running" title={RUNNING_HINT.replace("%s", text)}>
      {text}
    </span>
  );
}

export default function ElapsedTimer({ startedAt, finalMs, running = false }) {
  const { kind, ms } = timerState({ startedAt, finalMs, running });
  if (kind === "none") return null;
  if (kind === "running") return <RunningTimer startedAt={startedAt} />;
  const text = formatDuration(ms);
  return (
    <span className="elapsed" title={SETTLED_HINT.replace("%s", text)}>
      {text}
    </span>
  );
}
