// How much of the sign-in is left, and what the page should do about it.
//
// Under `AGENT_SSO_ENABLED` the platform calls the agent server as whoever is
// signed in, which makes the SSO session a resource a run *spends*. An
// Evaluation is minutes and an Optimize is hours; a session that ends underneath
// one stops it (`app/agent_sso.py`).
//
// The rule lives here rather than in a component for the reason in
// `frontend/CLAUDE.md` — `node --test` cannot load JSX, so a rule left inside a
// component is a rule that will never be tested — and because three screens ask
// the same question and must not answer it differently.
//
// **Three levels, and the middle one is the point.** A check that only said
// "expired / not expired" would be a check that fires at the worst possible
// moment: after six wizard steps, or ten minutes into a run. `warning` exists so
// the reader is told while there is still time to do something cheap about it,
// and the default window is deliberately much wider than any single action needs.
//
// Read from the *refresh* token, not the access token. The access token lives
// ten minutes and keycloak-js renews it silently; the refresh token is what
// bounds the session as a whole, and it is the one that cannot be renewed.

// Show the banner this far out. Wide on purpose: the cost of telling someone
// early is one dismissible line, the cost of telling them late is a lost run.
export const WARN_WITHIN_S = 15 * 60;

// Refuse to *start* new long-running work inside this window. Narrower than the
// warning, because blocking is the rude half and should only happen when
// starting really is futile — the entry gate, not the nag.
export const BLOCK_WITHIN_S = 2 * 60;

/**
 * Seconds of session left, or `null` when that is unknowable.
 *
 * `null` rather than 0 for "no expiry known": fake mode has no token at all, and
 * treating an absent expiry as an expired one would block every button in local
 * development.
 */
export function secondsLeft(refreshExpUnix, nowMs = Date.now()) {
  if (typeof refreshExpUnix !== "number" || !Number.isFinite(refreshExpUnix)) return null;
  return Math.round(refreshExpUnix - nowMs / 1000);
}

/**
 * The session's state, as the whole UI should read it.
 *
 * Returns `{ level, secondsLeft, message }` where level is one of:
 *
 *   ok       nothing to say
 *   warning  say so, but block nothing — there is still time
 *   expired  do not start new work; it would stop almost immediately
 */
export function sessionState({
  refreshExpUnix,
  nowMs = Date.now(),
  enabled = true,
  warnWithinS = WARN_WITHIN_S,
  blockWithinS = BLOCK_WITHIN_S,
} = {}) {
  const left = secondsLeft(refreshExpUnix, nowMs);
  // Not a keycloak deployment, or SSO forwarding is off: the agent server is
  // not reached as this user, so their session bounds nothing.
  if (!enabled || left === null) {
    return { level: "ok", secondsLeft: left, message: "" };
  }
  if (left <= blockWithinS) {
    return {
      level: "expired",
      secondsLeft: left,
      message:
        "Your sign-in has expired or is about to. Runs are sent to the agent " +
        "server as you, so sign in again before starting one.",
    };
  }
  if (left <= warnWithinS) {
    return {
      level: "warning",
      secondsLeft: left,
      message:
        `Your sign-in ends in about ${formatLeft(left)}. Runs in progress will ` +
        "stop when it does — sign in again to extend it.",
    };
  }
  return { level: "ok", secondsLeft: left, message: "" };
}

/**
 * "12 minutes" / "45 seconds", rounded the way a person would say it.
 *
 * The switch is at a whole minute rather than at 90 seconds, which would make
 * "1 minute" unreachable: 89s reads as seconds and 90s rounds straight to two.
 */
export function formatLeft(seconds) {
  const s = Math.max(Math.round(seconds), 0);
  if (s < 60) return `${s} second${s === 1 ? "" : "s"}`;
  const m = Math.round(s / 60);
  return `${m} minute${m === 1 ? "" : "s"}`;
}

/**
 * Why a Start button is disabled, or `null` when it is not.
 *
 * Separate from `sessionState` so the entry points read as a policy question
 * ("may I start this?") rather than re-deriving one from a level, which is how
 * two screens end up disagreeing about what `warning` means. Only `expired`
 * blocks: a warning is information, not a refusal.
 */
export function blockedReason(state) {
  if (!state || state.level !== "expired") return null;
  return state.message;
}

/**
 * What stopped an optimization run, in the reader's terms.
 *
 * `interrupted` now has two causes and they need different next actions: a
 * backend restart is resumable right now, an expired sign-in is resumable only
 * after signing in again. Told apart by the message the backend stored, because
 * that is the only thing on the wire that distinguishes them — see
 * `app/agent_sso.py` for the sentences it writes.
 */
export function interruptedReason(errorMessage) {
  const text = (errorMessage || "").toLowerCase();
  const bySession =
    text.includes("sso session") ||
    text.includes("refresh this session") ||
    text.includes("session") && text.includes("sign in");
  if (bySession) {
    return {
      kind: "session",
      summary: "Stopped when your sign-in ended. Every finished step is kept.",
      action: "Sign in again, then resume.",
    };
  }
  return {
    kind: "restart",
    summary: "Stopped mid-loop by a restart. Every finished step is kept.",
    action: "Resume to continue from the last finished step.",
  };
}
