// Undo for the split editor.
//
// Every edit on the step goes through here, not just the bulk ones. A rule the
// developer has to remember — "which of these can I take back?" — is worse than
// no rule at all, and the bulk buttons are only worth pressing if pressing them
// is cheap to regret: `Exclude all` on a column of sixty is one click and, with
// nothing behind it, twenty minutes of rebuilding.
//
// A snapshot is cheap. `withLists` in optimize_split.js shares the `questions`
// array and the `byKey` Map between versions, so what is stored per entry is
// three arrays of keys and nothing else — which is why this can afford to
// record every edit rather than choosing which ones deserve it.
//
// **The trap this module exists to make visible.** The split is not only edited
// by the developer: `Wizard.jsx` rebuilds it from scratch whenever the skill
// selection changes, because a different skill means a different set of
// questions. A history that survives that rebuild will happily restore the
// *previous* skill's split — and `makeSplit` filters unknown keys, so the result
// is not an error but a silently half-empty editor. Hence `reset`, and hence the
// fact that it is called from the same place the rebuild happens.

// Deep enough to cover a session of rearranging, shallow enough that nobody is
// scrolling back through it. The cost of the cap is the oldest entry, which is
// the one least likely to be wanted.
export const LIMIT = 50;

export const empty = () => [];

/** Record the split as it was *before* an edit. */
export function push(history, split) {
  const next = [...history, split];
  return next.length > LIMIT ? next.slice(next.length - LIMIT) : next;
}

/**
 * The split before the last recorded edit, and the history without it.
 *
 * Returns `{ history, split }` with `split` null when there is nothing to undo,
 * so the caller has one shape to handle rather than a sentinel to test for.
 */
export function undo(history) {
  if (!history.length) return { history, split: null };
  return { history: history.slice(0, -1), split: history[history.length - 1] };
}

export const canUndo = (history) => history.length > 0;

/** Forget everything. Called wherever the split is rebuilt rather than edited. */
export const reset = empty;
