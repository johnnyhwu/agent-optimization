// Run with: pnpm test  (node --test)
//
// What "how long did this run take" means in each of the states a run can end
// up in. Two of these are only reachable through an accident — a backend
// restart, and a resume days later — and both produce a number that looks
// perfectly ordinary while being wrong by an order of magnitude, which is why
// they are pinned here rather than left to a subtraction in the header.
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { activeMs, durationLabel, formatSpan, runDuration } from "./optimize_duration.js";
import { _internals } from "./useElapsed.js";

beforeEach(() => _internals.reset());

const iso = (ms) => new Date(ms).toISOString();
const T0 = Date.parse("2026-08-17T09:00:00.000Z");
const mins = (n) => n * 60_000;

function step(no, { from, to } = {}) {
  return {
    step_no: no,
    started_at: from == null ? null : iso(from),
    completed_at: to == null ? null : iso(to),
  };
}

// --- formatSpan -------------------------------------------------------------

test("a run's duration reads in the units a run is measured in", () => {
  // `formatDuration` in useElapsed.js stops at minutes because it times a single
  // question. An optimization run is an hour or more, and "83m 12s" is a number
  // the reader has to divide before it means anything.
  assert.equal(formatSpan(48_000), "48s");
  assert.equal(formatSpan(mins(12) + 30_000), "12m 30s");
  assert.equal(formatSpan(mins(83) + 12_000), "1h 23m");
  assert.equal(formatSpan(mins(60)), "1h 00m");
});

test("an unknown duration formats to nothing rather than to zero", () => {
  assert.equal(formatSpan(null), null);
  assert.equal(formatSpan(undefined), null);
  assert.equal(formatSpan(Number.NaN), null);
});

// --- the four kinds ---------------------------------------------------------

test("a finished run is measured from its own two stamps", () => {
  const run = {
    status: "completed",
    started_at: iso(T0),
    completed_at: iso(T0 + mins(72)),
  };
  const { kind, ms } = runDuration(run, []);
  assert.equal(kind, "settled");
  assert.equal(ms, mins(72));
});

test("a cancelled run took the time it took", () => {
  // Cancelling is a decision, not a failure: the steps it finished are kept and
  // downloadable, so the time it spent is as real as a completed run's.
  const run = {
    status: "cancelled",
    started_at: iso(T0),
    completed_at: iso(T0 + mins(9)),
  };
  assert.equal(runDuration(run, []).kind, "settled");
});

test("a running run is measured against the server's clock", () => {
  // The reader's clock is routinely seconds off; a slow one would otherwise
  // subtract to a negative duration on a run that just started.
  const startedAt = new Date(Date.now() - 5000).toISOString();
  const { kind, ms } = runDuration({ status: "running", started_at: startedAt }, []);
  assert.equal(kind, "running");
  assert.ok(ms >= 4900 && ms <= 5100, `got ${ms}`);
});

test("a pending run is already on the clock", () => {
  // It is queued, and the queue is time the developer is waiting through.
  const startedAt = new Date(Date.now() - 3000).toISOString();
  assert.equal(runDuration({ status: "pending", started_at: startedAt }, []).kind, "running");
});

test("an interrupted run is measured to its last finished step, not to now", () => {
  // The reaper leaves `completed_at` NULL on purpose — an interrupted run has
  // not completed, it can be resumed. Treating it as still running would count
  // every hour since the restart, so a run killed on Friday would read "3d 04h"
  // on Monday morning.
  const run = { status: "interrupted", started_at: iso(T0), completed_at: null };
  const steps = [
    step(0, { from: T0, to: T0 + mins(6) }),
    step(1, { from: T0 + mins(6), to: T0 + mins(20) }),
    step(2, { from: T0 + mins(20) }), // the one the restart killed
  ];
  const { kind, ms } = runDuration(run, steps);
  assert.equal(kind, "partial");
  assert.equal(ms, mins(20));
});

test("an interrupted run with nothing finished has nothing to report", () => {
  const run = { status: "interrupted", started_at: iso(T0), completed_at: null };
  assert.equal(runDuration(run, [step(0, { from: T0 })]).kind, "none");
});

test("a run with no start time reports nothing rather than 1970", () => {
  assert.equal(runDuration({ status: "completed", started_at: null }, []).kind, "none");
  assert.equal(runDuration(null, []).kind, "none");
});

test("a clock that went backwards mid-run does not produce a minus sign", () => {
  const run = {
    status: "completed",
    started_at: iso(T0),
    completed_at: iso(T0 - mins(1)),
  };
  assert.equal(runDuration(run, []).ms, 0);
});

// --- active time ------------------------------------------------------------

test("the steps' own durations add up independently of the wall clock", () => {
  // Resuming does not reset `started_at`, so the span of a run interrupted on
  // Friday and resumed on Monday is three days. What it actually spent working
  // is the sum of its steps, and both numbers are true.
  const steps = [
    step(0, { from: T0, to: T0 + mins(5) }),
    step(1, { from: T0 + mins(5), to: T0 + mins(25) }),
    step(2, { from: T0 + mins(4000), to: T0 + mins(4015) }), // after the resume
  ];
  assert.equal(activeMs(steps), mins(40));
});

test("steps that arrived on the stream carry no timestamps and no total", () => {
  // `optimize_steps.js` writes none — no event carries them — so this is null
  // until a snapshot or a refetch replaces the map. It must therefore never be
  // the headline number, or it would vanish and reappear while the run streams.
  assert.equal(activeMs([{ step_no: 1, status: "running" }]), null);
  assert.equal(activeMs([]), null);
  assert.equal(activeMs(undefined), null);
});

test("a step whose stamps are out of order is skipped, not subtracted", () => {
  assert.equal(activeMs([step(1, { from: T0 + mins(5), to: T0 })]), null);
});

// --- the label --------------------------------------------------------------

test("the label names which of the three numbers it is showing", () => {
  const finished = durationLabel(
    { status: "completed", started_at: iso(T0), completed_at: iso(T0 + mins(72)) },
    [],
  );
  assert.equal(finished.text, "ran for 1h 12m");

  const interrupted = durationLabel(
    { status: "interrupted", started_at: iso(T0), completed_at: null },
    [step(0, { from: T0, to: T0 + mins(20) })],
  );
  assert.equal(interrupted.text, "ran for 20m 00s up to the restart");

  const running = durationLabel(
    { status: "running", started_at: new Date(Date.now() - 61_000).toISOString() },
    [],
  );
  assert.match(running.text, /^running for 1m /);
});

test("a resumed run's tooltip accounts for the time it was not working", () => {
  // The gap between the span and the steps' own total is the hours the run sat
  // waiting for someone to press Resume. Reporting only the span makes a
  // fifteen-minute run look like a three-day one.
  const label = durationLabel(
    {
      status: "completed",
      started_at: iso(T0),
      completed_at: iso(T0 + mins(4020)),
    },
    [
      step(0, { from: T0, to: T0 + mins(5) }),
      step(1, { from: T0 + mins(4000), to: T0 + mins(4015) }),
    ],
  );
  assert.equal(label.text, "ran for 67h 00m");
  assert.match(label.title, /20m 00s of that/);
});

test("an ordinary run does not explain a gap it does not have", () => {
  const label = durationLabel(
    { status: "completed", started_at: iso(T0), completed_at: iso(T0 + mins(20)) },
    [
      step(0, { from: T0, to: T0 + mins(5) }),
      step(1, { from: T0 + mins(5), to: T0 + mins(19) }),
    ],
  );
  assert.equal(label.title, "Start to finish.");
});

test("a run with nothing to say says nothing rather than an empty phrase", () => {
  assert.equal(durationLabel({ status: "interrupted", started_at: iso(T0) }, []), null);
});
