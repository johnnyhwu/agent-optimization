// How a failed question is described, from the `failure_kind` the backend
// records alongside the message.
//
// The kind is used rather than the message text: matching on prose would break
// the first time the wording changed, and the backend already answers the
// question directly. `'agent' | 'agent_timeout' | 'judge' | 'judge_timeout' |
// 'judge_invalid'`, or null on rows written before the column existed.
//
// A timeout gets its own treatment because it is a different kind of news from
// a crash. Nothing is broken — a limit was reached — and what to do about it is
// a specific, nearby setting rather than a bug report. The banner therefore
// names the limit's own screen, and the list marks the row so that "half of
// these timed out" is visible without opening any of them.

export function isTimeout(failureKind) {
  return typeof failureKind === "string" && failureKind.endsWith("_timeout");
}

export function timeoutTitle(failureKind) {
  return failureKind === "judge_timeout"
    ? "The grading model ran out of time."
    : "The agent ran out of time.";
}

// Where the limit that stopped this actually lives. The agent's is per-run and
// on screen; the grading model's is not — saying so is the difference between
// looking for a field and knowing there isn't one.
export function timeoutAdvice(failureKind, { playground = false } = {}) {
  if (failureKind === "judge_timeout") {
    return "The grading model's time limit is set on the server rather than per run, so this one cannot be raised from here.";
  }
  return playground
    ? "Raise Timeout where you connected to the agent and ask again, or check whether the agent server is stuck on this question."
    : "Raise Timeout in the run's connection settings and trigger again, or check whether the agent server is stuck on this question.";
}
