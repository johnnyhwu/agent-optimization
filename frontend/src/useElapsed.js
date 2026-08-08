import { useSyncExternalStore } from "react";

// The clock behind every "how long has this been running" in the app.
//
// The obvious implementation — a `setInterval` inside each row, or one interval
// that re-renders the list — is what makes a screen like this feel heavy: an
// attempt row carries badges, tag chips and four buttons, and re-rendering
// twenty of them every second to change two characters of text is a second of
// work per second. Three things keep this cheap instead:
//
//   1. **One ticker for the whole app**, not one per timer. Every live number on
//      screen is driven by the same beat.
//   2. **The interval only exists while something is being timed.** Subscribers
//      are counted; the last one to leave stops the clock, so a screen with
//      nothing running costs exactly nothing.
//   3. **Only leaves subscribe.** `useSyncExternalStore` re-renders precisely
//      the components that called it, so a tick repaints the timer text and
//      nothing above it. That is what `ElapsedTimer` is for, and it is the
//      difference between repainting a few text nodes and repainting a list.
//
// Nothing is accumulated between ticks: elapsed is always recomputed as
// `now - startedAt`. So a missed tick is not a lost second, which is what makes
// pausing in a hidden tab free rather than something to compensate for.

// 1Hz. The value is displayed to the nearest 0.1s at most, so anything faster
// would be work nobody can see. Deliberately not requestAnimationFrame: this is
// a clock, not an animation, and it must keep its cost when the tab is busy.
const TICK_MS = 1000;

let timer = null;
const subscribers = new Set();

// How far this browser's clock is ahead of (or behind) the server's, in ms.
// Durations are measured against timestamps the server produced, so on a machine
// whose clock has drifted — which is most of them, by seconds — an uncorrected
// subtraction shows a number that is simply wrong, and a slow clock shows a
// negative one. Corrected once per stream connection, from the snapshot the
// server sends when it opens.
let skewMs = 0;

// The value `useSyncExternalStore` compares between renders. It changes only in
// `tick`, and that is deliberate: React calls `getSnapshot` several times per
// render and warns (or re-renders again) if the answer changes underneath it, so
// reading the clock there would make every render that straddled a second
// boundary do extra work for nothing. The snapshot is a *change signal*; the
// actual duration is read from the clock at render time by `elapsedSince`.
let snapshot = 0;

function tick() {
  snapshot += 1;
  // Copied before iterating: a subscriber may unsubscribe as a result of the
  // render this notification triggers.
  [...subscribers].forEach((fn) => fn());
}

// Guarded the same way the listener below is: this module is imported by plain
// Node for its tests, where there is no `document` to ask.
const hidden = () => typeof document !== "undefined" && document.hidden;

function start() {
  if (timer === null && subscribers.size > 0 && !hidden()) {
    timer = setInterval(tick, TICK_MS);
  }
}

function stop() {
  if (timer !== null) {
    clearInterval(timer);
    timer = null;
  }
}

// A hidden tab is a tab nobody is reading, and browsers throttle its timers
// anyway. Stopping outright is both cheaper and more honest — and costs nothing
// on return, because the first render after `visibilitychange` recomputes from
// the timestamp rather than from a count of elapsed ticks.
if (typeof document !== "undefined") {
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      stop();
    } else {
      start();
      tick(); // repaint immediately rather than up to a second later
    }
  });
}

function subscribe(fn) {
  subscribers.add(fn);
  start();
  return () => {
    subscribers.delete(fn);
    if (subscribers.size === 0) stop();
  };
}

const getSnapshot = () => snapshot;

/** Re-render this component once a second, for as long as it is mounted. */
export function useTick() {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

/**
 * Note the server's clock, from a progress stream's snapshot.
 *
 * Called on every (re)connect: a long-lived stream outlives any one measurement,
 * and reconnecting is the natural moment to take a fresh one.
 */
export function setServerTime(iso) {
  if (!iso) return;
  const server = Date.parse(iso);
  if (Number.isFinite(server)) skewMs = server - Date.now();
}

/** Milliseconds since `startedAt`, on the server's clock. Null if unknown. */
export function elapsedSince(startedAt) {
  if (!startedAt) return null;
  const start = Date.parse(startedAt);
  if (!Number.isFinite(start)) return null;
  // Never negative: a timestamp a moment in the server's future (the stamp
  // arriving before the skew measurement settles) should read as 0s, not as a
  // minus sign.
  return Math.max(0, Date.now() + skewMs - start);
}

/**
 * A duration for display.
 *
 * Sub-minute values carry a decimal because that is the range where the
 * difference between 8.4s and 12.1s is the point; past a minute the tenth is
 * noise and `1m 04s` reads faster than `64.3s`.
 */
export function formatDuration(ms) {
  if (ms === null || ms === undefined) return null;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${String(Math.floor(seconds % 60)).padStart(2, "0")}s`;
}

/**
 * What a timer should show, given everything known about one row.
 *
 * Pure, and deliberately not inlined into `ElapsedTimer`: this is the decision
 * that got it wrong once already, and as JSX it could only be checked by driving
 * a browser. Here every case is a unit test.
 *
 * Returns `{ kind: "settled" | "running" | "none", ms }`.
 *
 *   settled  the agent answered and the server measured how long it took
 *   running  the agent has the question right now
 *   none     nothing truthful to show
 *
 * `running` has to be told, not inferred. A measured duration only exists once
 * the agent has answered, so treating "no duration" as "still going" leaves
 * every timeout, transport error and stop press counting upward for as long as
 * the page is open. The eval side makes that vivid: a backend restart marks the
 * *run* failed but leaves its question rows 'pending', so a run interrupted last
 * week would open with a timer counting from last week.
 */
export function timerState({ startedAt, finalMs, running = false } = {}) {
  // A measured duration wins over everything, including a row that has since
  // failed — an agent that answered and then failed still took the time it took.
  if (finalMs !== null && finalMs !== undefined) return { kind: "settled", ms: finalMs };
  if (!running || !startedAt) return { kind: "none", ms: null };
  const ms = elapsedSince(startedAt);
  if (ms === null) return { kind: "none", ms: null };
  // Whole seconds while moving: a flickering tenth on a number that is still
  // changing is noise, and it is the settled value that deserves the precision.
  return { kind: "running", ms: Math.floor(ms / 1000) * 1000 };
}

// Exported for the tests, which need a clean clock between cases.
export const _internals = {
  reset() {
    stop();
    subscribers.clear();
    skewMs = 0;
    snapshot = 0;
  },
  tick,
  subscribe,
  get snapshot() {
    return snapshot;
  },
  get skewMs() {
    return skewMs;
  },
  get running() {
    return timer !== null;
  },
  get subscriberCount() {
    return subscribers.size;
  },
};
