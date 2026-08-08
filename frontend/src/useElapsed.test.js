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
} from "./useElapsed.js";

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
