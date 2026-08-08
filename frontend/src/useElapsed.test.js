// Run with: pnpm test  (node --test)
//
// The arithmetic behind every elapsed time on screen. Worth pinning down because
// two of these cases are only ever hit on someone else's machine: a browser
// clock that disagrees with the server's is invisible in development and
// produces a plainly wrong number in production, and the negative case is what
// that looks like when the clock is slow rather than fast.
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import {
  _internals,
  elapsedSince,
  formatDuration,
  setServerTime,
  timerState,
} from "./useElapsed.js";

const ago = (s) => new Date(Date.now() - s * 1000).toISOString();

beforeEach(() => _internals.reset());

test("a duration under a minute keeps its tenth", () => {
  assert.equal(formatDuration(8400), "8.4s");
  assert.equal(formatDuration(0), "0.0s");
  assert.equal(formatDuration(59_900), "59.9s");
});

test("past a minute the tenth is noise and minutes read faster", () => {
  assert.equal(formatDuration(60_000), "1m 00s");
  assert.equal(formatDuration(64_300), "1m 04s");
  assert.equal(formatDuration(3_723_000), "62m 03s");
});

test("an unknown duration formats to nothing rather than to zero", () => {
  assert.equal(formatDuration(null), null);
  assert.equal(formatDuration(undefined), null);
});

test("elapsed is measured from the timestamp, not accumulated", () => {
  const startedAt = new Date(Date.now() - 5000).toISOString();
  const elapsed = elapsedSince(startedAt);
  assert.ok(elapsed >= 4900 && elapsed <= 5100, `got ${elapsed}`);
});

test("a browser clock that runs fast is corrected, not believed", () => {
  // The server says it is 30s earlier than this machine thinks. A question
  // started 5s ago on the server's clock must read 5s, not 35s.
  setServerTime(new Date(Date.now() - 30_000).toISOString());
  const startedAt = new Date(Date.now() - 35_000).toISOString();

  const elapsed = elapsedSince(startedAt);
  assert.ok(elapsed >= 4900 && elapsed <= 5100, `got ${elapsed}`);
});

test("a clock that runs slow never produces a negative duration", () => {
  // Server ahead of this machine: a just-started question would otherwise
  // subtract to a minus sign.
  setServerTime(new Date(Date.now() + 30_000).toISOString());
  assert.equal(elapsedSince(new Date(Date.now() + 30_000).toISOString()), 0);
});

test("an unusable server time leaves the previous correction alone", () => {
  setServerTime(new Date(Date.now() - 10_000).toISOString());
  const before = _internals.skewMs;

  setServerTime("not a date");
  setServerTime(null);
  setServerTime(undefined);

  assert.equal(_internals.skewMs, before);
});

test("a missing or unparseable start time reads as unknown, not as zero", () => {
  assert.equal(elapsedSince(null), null);
  assert.equal(elapsedSince(undefined), null);
  assert.equal(elapsedSince(""), null);
  assert.equal(elapsedSince("whenever"), null);
});

test("the ticker does not run until something is being timed", () => {
  // The property that makes an idle screen free: no subscribers, no interval.
  assert.equal(_internals.subscriberCount, 0);
  assert.equal(_internals.running, false);
});

// --- What a timer decides to show ------------------------------------------
//
// The decision that got it wrong once: a row with a start time and no measured
// duration was read as "still going", so every attempt that ended without an
// answer counted upward for as long as the page stayed open. These pin down all
// three outcomes, including every way a row can end without one.

test("a question with the agent right now counts up", () => {
  const { kind, ms } = timerState({ startedAt: ago(8), running: true });
  assert.equal(kind, "running");
  assert.equal(ms, 8000, "whole seconds while moving, not 8123ms");
});

test("an answered question shows what the server measured", () => {
  assert.deepEqual(
    timerState({ startedAt: ago(30), finalMs: 8400, running: false }),
    { kind: "settled", ms: 8400 }
  );
});

test("a measured duration survives the row failing afterwards", () => {
  // The agent answered and then something downstream failed. It still took the
  // time it took, and that is worth knowing.
  assert.deepEqual(
    timerState({ startedAt: ago(30), finalMs: 2600, running: false }),
    { kind: "settled", ms: 2600 }
  );
});

test("a row that ended without an answer shows nothing at all", () => {
  // A timeout, a transport error, a stop press, or an interrupted run: all
  // leave a start time and no duration. Showing the running branch here is the
  // bug -- it counts up forever on a row that is already dead. Inventing a
  // duration would be worse: how long it *would* have taken is not a question
  // this platform can answer.
  for (const startedAt of [ago(12), ago(60 * 60 * 24 * 7)]) {
    assert.deepEqual(
      timerState({ startedAt, finalMs: null, running: false }),
      { kind: "none", ms: null }
    );
  }
});

test("a question that has not started yet shows nothing", () => {
  assert.deepEqual(
    timerState({ startedAt: null, running: true }),
    { kind: "none", ms: null }
  );
});

test("a row from before start times were recorded shows nothing", () => {
  // question_results.started_at is nullable with no backfill, so historical
  // rows genuinely do not know. Silence beats a fabricated duration.
  assert.deepEqual(timerState({ startedAt: null, finalMs: null }), { kind: "none", ms: null });
  assert.deepEqual(timerState({}), { kind: "none", ms: null });
  assert.deepEqual(timerState(), { kind: "none", ms: null });
});

test("running is never inferred from the absence of a duration", () => {
  // The whole property, stated once: with no measured duration, `running` alone
  // decides -- and it defaults to not running, so a caller that forgets to pass
  // it gets silence rather than a runaway clock.
  const startedAt = ago(5);
  assert.equal(timerState({ startedAt, running: true }).kind, "running");
  assert.equal(timerState({ startedAt, running: false }).kind, "none");
  assert.equal(timerState({ startedAt }).kind, "none");
});

test("a zero-millisecond answer is a duration, not a missing one", () => {
  // `0` is falsy; a truthiness check here would silently drop the fastest
  // answers into the "nothing to show" branch.
  assert.deepEqual(timerState({ startedAt: ago(1), finalMs: 0 }), { kind: "settled", ms: 0 });
});

test("subscribing runs the clock; the last unsubscribe stops it", () => {
  const a = _internals.subscribe(() => {});
  assert.equal(_internals.running, true);
  const b = _internals.subscribe(() => {});
  assert.equal(_internals.subscriberCount, 2);

  a();
  assert.equal(_internals.running, true, "still one timer on screen");
  b();
  assert.equal(_internals.running, false, "nothing left to time");
  assert.equal(_internals.subscriberCount, 0);
});

test("the snapshot is stable between ticks", () => {
  // useSyncExternalStore calls getSnapshot several times per render and warns
  // (or renders again) if the answer moves underneath it. Reading the clock
  // there would make every render that straddled a second boundary do that.
  const before = _internals.snapshot;
  assert.equal(_internals.snapshot, before);
  assert.equal(_internals.snapshot, before);

  _internals.tick();
  assert.notEqual(_internals.snapshot, before);
});
