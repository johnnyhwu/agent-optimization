// Run with: pnpm test  (node --test)
//
// Both bugs this module exists to prevent were found by driving a browser, and
// both were in the same few lines pulling in opposite directions. These cases
// are the specification, so the next change to them has to argue with a test.
import { test } from "node:test";
import assert from "node:assert/strict";
import { adoptFetched, mergeAttempt, pruneById } from "./attempt_state.js";

const row = (over = {}) => ({
  id: "a1", question: "q", phase: "pending", status: "running",
  verdict: null, error_message: null,
  agent_started_at: null, agent_latency_ms: null, ...over,
});

const event = (over = {}) => ({
  attempt_id: "a1", phase: "answered", status: "running", verdict: null,
  error_message: null, agent_started_at: "2026-08-08T10:00:00+00:00",
  agent_latency_ms: null, ...over,
});

// --- mergeAttempt ----------------------------------------------------------

test("an event moves the row forward", () => {
  const merged = mergeAttempt(row(), event({ phase: "judged", verdict: "correct" }));
  assert.equal(merged.phase, "judged");
  assert.equal(merged.verdict, "correct");
});

test("a field the event omits keeps the value the row had", () => {
  // The completion event carries a latency; it must not blank the question text
  // or the fields it says nothing about.
  const merged = mergeAttempt(
    row({ question: "keep me", agent_started_at: "2026-08-08T10:00:00+00:00" }),
    { attempt_id: "a1", status: "done", phase: "traced", agent_latency_ms: 8400 }
  );
  assert.equal(merged.question, "keep me");
  assert.equal(merged.agent_started_at, "2026-08-08T10:00:00+00:00");
  assert.equal(merged.agent_latency_ms, 8400);
});

test("no event leaves the row untouched", () => {
  const before = row();
  assert.equal(mergeAttempt(before, null), before);
  assert.equal(mergeAttempt(before, undefined), before);
});

test("merging does not mutate the row it was given", () => {
  const before = row();
  mergeAttempt(before, event({ phase: "judged" }));
  assert.equal(before.phase, "pending");
});

// --- adoptFetched: a fetch that is older than an event ---------------------

test("a stale fetch cannot undo an event already applied", () => {
  // The bug: the response was served just before the attempt finished, and
  // replaced a completed row with a running one. Nothing further would arrive
  // to correct it, so the row stayed wrong for as long as the page was open.
  const fetched = [row({ status: "running", phase: "answered" })];
  const events = { a1: event({ status: "done", phase: "diagnosed", agent_latency_ms: 8400 }) };

  const [adopted] = adoptFetched(fetched, events);

  assert.equal(adopted.status, "done");
  assert.equal(adopted.phase, "diagnosed");
  assert.equal(adopted.agent_latency_ms, 8400);
});

test("a fetch and an event that agree leave the row untouched", () => {
  const settled = {
    status: "done", phase: "diagnosed", agent_latency_ms: 8400,
    agent_started_at: "2026-08-08T10:00:00+00:00",
  };
  const fetched = [row(settled)];
  const events = { a1: event(settled) };

  assert.deepEqual(adoptFetched(fetched, events)[0], fetched[0]);
});

test("attempts nothing has been heard about pass straight through", () => {
  const fetched = [row({ id: "other" })];
  assert.deepEqual(adoptFetched(fetched, { a1: event() })[0], fetched[0]);
});

// --- adoptFetched: after a gap ---------------------------------------------

test("after a reported gap the fetch stands alone", () => {
  // `resync` means the stream dropped something -- here, the completion. What
  // we remember is now OLDER than the truth, so re-applying it would paint the
  // finished row back to "answered": the recovery path re-creating the very
  // stale row it was triggered to repair. Clearing the memory first is the fix,
  // and passing `{}` is what that looks like here.
  const fetched = [row({ status: "done", phase: "diagnosed", agent_latency_ms: 8400 })];
  const stale = { a1: event({ status: "running", phase: "answered" }) };

  assert.equal(adoptFetched(fetched, stale)[0].status, "running", "the bug, stated");
  assert.equal(adoptFetched(fetched, {})[0].status, "done", "the fix");
  assert.equal(adoptFetched(fetched)[0].phase, "diagnosed");
});

// --- pruneById -------------------------------------------------------------

test("entries for attempts that are gone are dropped", () => {
  const map = { a1: { x: 1 }, gone: { x: 2 } };
  assert.deepEqual(pruneById(map, [row({ id: "a1" })]), { a1: { x: 1 } });
});

test("pruning against an empty list keeps nothing", () => {
  assert.deepEqual(pruneById({ a1: {}, a2: {} }, []), {});
});

test("pruning does not mutate the map it was given", () => {
  const map = { a1: {}, gone: {} };
  pruneById(map, []);
  assert.deepEqual(Object.keys(map), ["a1", "gone"]);
});
