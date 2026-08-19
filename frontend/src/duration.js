// A measured duration, in the units it is worth reading in.
//
// There were two identical copies of this, one per optimize component, and
// adding a third for the span detail is how a fourth eventually disagrees with
// the other three about what "—" means.
//
// One rule beyond the obvious: below a second, milliseconds. `0.2s` and `0.9s`
// are the same number to a reader skimming a column, and the whole reason a
// per-step duration is on screen is to find the step that is unlike its
// neighbours. Above a second the fraction is what carries that, so `2.4s` stays.

/** `840ms`, `2.4s`, or `—` when there is nothing measured. */
export function secs(ms) {
  if (ms == null) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}
