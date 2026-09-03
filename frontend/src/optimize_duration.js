// How long an optimization run took, and how to say it.
//
// A run is measured in hours, and until now the page said nothing about that at
// all: the header carried the moment it started and every screen after that
// answered "how far along is it", never "how long has this cost me". The two
// timestamps were already on the wire (`OptimizationRunOut.started_at` and
// `.completed_at`) and simply unread.
//
// Three cases, and the third is the reason this is a module rather than a
// subtraction inside the header:
//
//   * **Finished** — completed, cancelled or failed. The engine stamps
//     `completed_at` on every terminal path (`optimizer/engine.py`'s `finally`),
//     so the wall-clock span is honest.
//   * **Running** — measured against the server's clock, not the reader's. See
//     `useElapsed.js`: the browser's clock is routinely seconds off, and a slow
//     one produces a negative duration.
//   * **Interrupted** — a backend restart, which deliberately leaves
//     `completed_at` NULL because the run has *not* completed: it can be
//     resumed, and the backend's own test pins that ("an interrupted run has not
//     completed"). There is still a real number to show, and it is not "unknown"
//     — it is how far it had got when the process died, which is the last step
//     that finished. Reporting it as a whole span would silently include however
//     many days the run then sat waiting to be resumed.
//
// `activeMs` covers the same hazard from the other side. Resuming does not reset
// `started_at`, so a run that was interrupted on Friday and resumed on Monday
// has a wall-clock span of three days and perhaps forty minutes of work in it.
// The steps carry their own timestamps, so their sum is what the run actually
// spent computing — offered alongside the span, not instead of it, because the
// span is what the calendar says and both are true.

import { elapsedSince } from "./useElapsed.js";

const RUNNING = new Set(["running", "pending"]);

function stamp(value) {
  if (!value) return null;
  const at = Date.parse(value);
  return Number.isFinite(at) ? at : null;
}

/** The sum of every step's own measured duration, or null if none carries one.
 *
 * Steps arriving on the stream have no timestamps — `optimize_steps.js` writes
 * none, and no event carries them — so this is null until a snapshot or a
 * refetch has replaced the map. That is why it is a garnish and never the
 * headline: it would otherwise vanish and reappear as the page streams.
 */
export function activeMs(steps) {
  let total = 0;
  let seen = false;
  for (const step of steps || []) {
    const from = stamp(step.started_at);
    const to = stamp(step.completed_at);
    if (from == null || to == null || to < from) continue;
    total += to - from;
    seen = true;
  }
  return seen ? total : null;
}

/** The last moment this run is known to have been doing something. */
function lastActivity(steps) {
  let latest = null;
  for (const step of steps || []) {
    const at = stamp(step.completed_at);
    if (at != null && (latest == null || at > latest)) latest = at;
  }
  return latest;
}

/**
 * What a run's timer should say.
 *
 * Returns `{ kind, ms, activeMs }`:
 *
 *   settled   it finished, and this is how long it took
 *   running   it is going now, and this is how long so far
 *   partial   a restart interrupted it; this is how long up to the last step
 *             that finished, and there is no end to measure against
 *   none      nothing truthful to show
 */
export function runDuration(run, steps = []) {
  const active = activeMs(steps);
  const none = { kind: "none", ms: null, activeMs: active };
  if (!run) return none;

  const started = stamp(run.started_at);
  if (started == null) return none;

  const finished = stamp(run.completed_at);
  if (finished != null) {
    // Never negative. A clock adjustment on the server between the two stamps
    // is rare and a minus sign in the header is not the way to report it.
    return { kind: "settled", ms: Math.max(0, finished - started), activeMs: active };
  }

  if (RUNNING.has(run.status)) {
    const ms = elapsedSince(run.started_at);
    return ms == null ? none : { kind: "running", ms, activeMs: active };
  }

  // Interrupted, or any other terminal state that never got its stamp.
  const last = lastActivity(steps);
  if (last == null || last < started) return none;
  return { kind: "partial", ms: last - started, activeMs: active };
}

/**
 * A run-length duration, to the minute.
 *
 * Not `formatDuration` from `useElapsed.js`, which is for the seconds a single
 * question spends with the agent. A run is measured in hours, and its seconds
 * are noise at every scale it is read at — but they were not merely useless,
 * they moved the page. This string is live while a run goes, it sits in a
 * `.opt-fact-sub` inside a fixed-width grid cell, and `running for 1m 01s` is
 * wide enough to wrap where `running for 59s` did not: the facts row grew 16.5px
 * and pushed everything under it down, once a minute, for the length of the run.
 *
 * Reporting to the minute is most of the fix — the value now changes 60 times
 * less often — and `.opt-fact-sub` not wrapping is the rest of it, because
 * `59m` to `1h 00m` is still two characters wider.
 *
 * The sub-minute case is a phrase rather than `0m`, which reads as a stopped
 * clock or a bug. It costs the one place a second would have been informative,
 * and buys a header that does not move.
 */
export function formatSpan(ms) {
  if (ms == null || !Number.isFinite(ms)) return null;
  const minutes = Math.max(0, Math.floor(ms / 60_000));
  if (minutes < 1) return "under a minute";
  if (minutes < 60) return `${minutes}m`;
  return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, "0")}m`;
}

// How far apart the wall clock and the summed step times have to be before
// saying both is worth the words. Below this it is the ordinary gap — the
// pre-flight rollout, the pauses between steps — and not the story.
const DRIFT_MS = 60_000;

/**
 * The sentence under the header's Started fact.
 *
 * One function so the phrasing and the tooltip cannot drift apart, and so every
 * case above is covered by a test rather than by a chain of ternaries in JSX.
 */
export function durationLabel(run, steps = []) {
  const { kind, ms, activeMs: active } = runDuration(run, steps);
  if (kind === "none") return null;
  const span = formatSpan(ms);

  if (kind === "running") {
    return { text: `running for ${span}`, title: "Since this run was started." };
  }
  if (kind === "partial") {
    return {
      text: `ran for ${span} up to the restart`,
      title:
        "The backend restarted before this run finished, so it has no end time. " +
        "This is the span from its start to the last step that completed.",
    };
  }

  // Settled. The gap between the two numbers is the interesting part when a run
  // was resumed: the span includes however long it sat waiting for someone to
  // press the button.
  const gap = active != null && ms != null && ms - active > DRIFT_MS;
  return {
    text: `ran for ${span}`,
    title: gap
      ? `Start to finish. Its steps account for ${formatSpan(active)} of that; ` +
        "the rest is time the run spent waiting rather than working."
      : "Start to finish.",
  };
}
