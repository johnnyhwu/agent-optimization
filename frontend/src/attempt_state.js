// Reconciling the playground's attempt list against two sources that disagree.
//
// The list is fed by a REST fetch and by a live stream, and nothing orders them
// against each other. That has already produced two bugs, in opposite
// directions, which is why this logic lives here as plain functions rather than
// inside the component: as JSX it could only be checked by driving a browser.
//
//   1. **A fetch can be older than an event.** The response reflects the store
//      at the moment it was *served*, which can precede an event already
//      applied. Taken raw, a response served just before an attempt finished
//      repaints a completed row back to running — and with no further events
//      coming, it stays that way. So the newest known event is re-applied on
//      top.
//
//   2. **After a gap, the newest known event is itself stale.** `resync` means
//      the stream dropped something, so what we remember may be older than the
//      truth. Re-applying it then reverts precisely the attempt whose ending we
//      missed: the recovery path re-creating the stale row it exists to repair.
//      So a gap clears the memory first, and the fetch stands alone.
//
// The two together are the whole rule: **remembered events win over a fetch,
// unless we know the memory has a hole.**

/**
 * One list row updated by one progress event.
 *
 * `??` throughout, so a field the event does not carry keeps what the row
 * already had rather than being blanked. A null event leaves the row alone,
 * which is what makes "merge whatever we remember, if anything" a single
 * expression at both call sites.
 */
export function mergeAttempt(row, event) {
  if (!event) return row;
  return {
    ...row,
    phase: event.phase ?? row.phase,
    status: event.status ?? row.status,
    verdict: event.verdict ?? row.verdict,
    error_message: event.error_message ?? row.error_message,
    // Carried on the event, so a finished row no longer costs a refetch just to
    // learn how long the agent took.
    agent_started_at: event.agent_started_at ?? row.agent_started_at,
    agent_latency_ms: event.agent_latency_ms ?? row.agent_latency_ms,
    // Counted off the trace, so it lands with `attempt_traced` — later than the
    // verdict, and null on every event before it. `??` for the same reason as
    // the two above: a plain assignment would blank the number on the next
    // event to arrive.
    llm_call_count: event.llm_call_count ?? row.llm_call_count,
  };
}

/**
 * A freshly fetched list, with anything the stream has since said folded in.
 *
 * Pass `{}` for `events` when a gap has been reported — see the note above.
 */
export function adoptFetched(fetched, events = {}) {
  return fetched.map((row) => mergeAttempt(row, events[row.id]));
}

/**
 * Drop entries for attempts that no longer exist.
 *
 * The id-keyed maps beside the list would otherwise keep an entry for every
 * attempt ever seen — including ones deleted here and ones the server evicted
 * at its per-user cap — for as long as the tab stays open.
 */
export function pruneById(map, rows) {
  const live = new Set(rows.map((r) => r.id));
  return Object.fromEntries(Object.entries(map).filter(([id]) => live.has(id)));
}
