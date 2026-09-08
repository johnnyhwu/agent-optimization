import test from "node:test";
import assert from "node:assert/strict";
import {
  BLOCK_WITHIN_S,
  WARN_WITHIN_S,
  blockedReason,
  formatLeft,
  interruptedReason,
  secondsLeft,
  sessionState,
} from "./session_expiry.js";

// A fixed "now" so every case reads as a number of seconds left, not as a date.
const NOW_MS = 1_700_000_000_000;
const at = (secondsFromNow) => NOW_MS / 1000 + secondsFromNow;

const state = (secondsFromNow, extra = {}) =>
  sessionState({ refreshExpUnix: at(secondsFromNow), nowMs: NOW_MS, ...extra });

test("a session with hours left says nothing", () => {
  assert.equal(state(3 * 3600).level, "ok");
  assert.equal(state(3 * 3600).message, "");
});

test("a session inside the warning window warns but does not block", () => {
  const s = state(WARN_WITHIN_S - 60);
  assert.equal(s.level, "warning");
  assert.equal(blockedReason(s), null, "a warning is information, not a refusal");
  assert.match(s.message, /sign in again/i);
});

test("a session inside the blocking window blocks", () => {
  const s = state(BLOCK_WITHIN_S - 1);
  assert.equal(s.level, "expired");
  assert.equal(blockedReason(s), s.message);
});

test("an already-expired session blocks rather than reading as ok", () => {
  assert.equal(state(-500).level, "expired");
});

test("the warning window is wider than the blocking window", () => {
  // Otherwise "expired" would be reached without ever passing through a state
  // that told the reader while there was still time to act.
  assert.ok(WARN_WITHIN_S > BLOCK_WITHIN_S);
});

test("the boundaries are inclusive on the blocking side", () => {
  assert.equal(state(BLOCK_WITHIN_S).level, "expired");
  assert.equal(state(BLOCK_WITHIN_S + 1).level, "warning");
  assert.equal(state(WARN_WITHIN_S).level, "warning");
  assert.equal(state(WARN_WITHIN_S + 1).level, "ok");
});

test("an unknown expiry is ok, never expired", () => {
  // fake mode has no token at all. Treating an absent expiry as an expired one
  // would disable every Start button in local development.
  for (const value of [undefined, null, NaN, "soon"]) {
    const s = sessionState({ refreshExpUnix: value, nowMs: NOW_MS });
    assert.equal(s.level, "ok", `expiry ${String(value)}`);
    assert.equal(s.secondsLeft, null);
  }
});

test("a deployment that does not forward the token is always ok", () => {
  // The agent server is not reached as this user, so their session bounds
  // nothing — warning them about it would be a warning they cannot act on.
  assert.equal(state(-500, { enabled: false }).level, "ok");
  assert.equal(state(10, { enabled: false }).message, "");
});

test("secondsLeft counts down against the given clock", () => {
  assert.equal(secondsLeft(at(90), NOW_MS), 90);
  assert.equal(secondsLeft(at(-30), NOW_MS), -30);
  assert.equal(secondsLeft("nope", NOW_MS), null);
});

test("the message says how long is left, in units a person would use", () => {
  assert.equal(formatLeft(45), "45 seconds");
  assert.equal(formatLeft(1), "1 second");
  assert.equal(formatLeft(59), "59 seconds");
  assert.equal(formatLeft(600), "10 minutes");
  // The singular has to be reachable. Switching to minutes at 90s instead of at
  // 60 would make it dead code: 89s reads as seconds and 90s rounds to two.
  assert.equal(formatLeft(60), "1 minute");
  assert.equal(formatLeft(80), "1 minute");
  assert.equal(formatLeft(-5), "0 seconds", "a negative remainder is not a negative count");
});

test("blockedReason is null for every level except expired", () => {
  assert.equal(blockedReason(null), null);
  assert.equal(blockedReason({ level: "ok" }), null);
  assert.equal(blockedReason({ level: "warning", message: "x" }), null);
  assert.equal(blockedReason({ level: "expired", message: "x" }), "x");
});

// --- what stopped an interrupted optimization run -------------------------

test("a restart and an expired sign-in are told apart", () => {
  // The two need different next actions: a restart is resumable right now, an
  // expired sign-in only after signing in again.
  const restart = interruptedReason("the backend restarted; this run can be resumed");
  assert.equal(restart.kind, "restart");
  assert.match(restart.summary, /restart/i);

  const session = interruptedReason(
    "the identity provider refused to refresh this session (HTTP 400)."
  );
  assert.equal(session.kind, "session");
  assert.match(session.action, /sign in/i);
});

test("an interrupted run with no message reads as a restart", () => {
  // The pre-existing case: every run interrupted before this feature existed.
  for (const value of [undefined, null, ""]) {
    assert.equal(interruptedReason(value).kind, "restart");
  }
});

test("the no-session-held message is recognised as a session problem", () => {
  // What the registry says after a backend restart lost its tokens.
  const r = interruptedReason(
    "no SSO session is held for this run. It was most likely started before the backend restarted."
  );
  assert.equal(r.kind, "session");
});
