// What one optimization run is called, and when it started.
//
// One function because there were two, in two components, disagreeing. The rail
// fell back to `new Date(started_at).toLocaleString()` and the panel beside it
// fell back to `Optimizing ${skill_name}`, so a run started without a name — the
// ordinary case, since the wizard's Name field offers its suggestion as a
// placeholder rather than a value — appeared in the list under a timestamp and
// on the page under a sentence. Two names for one thing, side by side, reads as
// two runs.
//
// The timestamp is not lost by unifying them: it moves to the row's second line,
// where the skill and the step count already are, and stops competing with the
// name for the row's one strong line.

import { shortStamp } from "./timestamp.js";

export function runTitle(run) {
  const name = (run?.name || "").trim();
  if (name) return name;
  if (run?.skill_name) return `Optimizing ${run.skill_name}`;
  return "Optimization run";
}

export function runStartedAt(run) {
  return shortStamp(run?.started_at);
}
