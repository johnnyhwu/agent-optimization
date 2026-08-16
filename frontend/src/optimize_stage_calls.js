// What happened to the analysts' patches on the way to the skill.
//
// A step's applied edits are not the edits any one analyst proposed. The
// per-minibatch patches are merged — failures together, successes together, then
// the two groups combined — and if the pool is still over the learning rate, a
// ranking call decides which survive. This module turns that list of calls into
// the four phases a person thinks in, with a one-line summary each.
//
// It is here rather than in the component for the reason `frontend/CLAUDE.md`
// gives: `node --test` can load a pure module and cannot load JSX, so a rule
// left inside a component is a rule that will never be tested.

const PHASES = [
  {
    key: "merge_failure",
    title: "Merging the failure analysts",
    blurb: "Every patch proposed from a failed trajectory, combined into one.",
  },
  {
    key: "merge_success",
    title: "Merging the success analysts",
    blurb: "The same, for the patterns worth reinforcing.",
  },
  {
    key: "merge_final",
    title: "Combining the two groups",
    blurb: "Failure-driven edits take priority over success-driven ones.",
  },
  {
    key: "ranking",
    title: "Ranking against the learning rate",
    blurb: "Runs only when the merged pool is larger than the step's budget.",
  },
];

/** How many edits a stage's answer carries, in whichever key it used. */
export function editCount(output) {
  if (!output) return null;
  for (const key of ["edits", "candidates", "suggestions"]) {
    if (Array.isArray(output[key])) return output[key].length;
  }
  if (Array.isArray(output.selected_indices)) return output.selected_indices.length;
  return null;
}

/** The one line worth reading without opening anything. */
export function summarise(call) {
  if (call.error) return call.error;
  const reasoning = String(call.output?.reasoning || "").trim();
  if (reasoning) return reasoning;
  const n = editCount(call.output);
  if (n != null) return `${n} ${n === 1 ? "edit" : "edits"} came out of this stage.`;
  return "This stage returned nothing to show.";
}

/**
 * The calls grouped into phases, in the order the pipeline runs them.
 *
 * Phases that did not happen are left out rather than shown as empty: ranking
 * is skipped whenever the pool already fits, and success merging never happens
 * in a failure-only run. An empty section would read as a stage that ran and
 * did nothing, which is a different and worse claim.
 *
 * Anything with an unrecognised stage name is collected at the end instead of
 * being dropped — a call that was made is evidence, whatever it was called.
 */
export function groupStageCalls(stageCalls = []) {
  const bySeq = [...stageCalls].sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0));
  const groups = [];

  for (const phase of PHASES) {
    const calls = bySeq.filter((c) => c.stage === phase.key);
    if (calls.length) groups.push({ ...phase, calls });
  }

  const known = new Set(PHASES.map((p) => p.key));
  const rest = bySeq.filter((c) => !known.has(c.stage));
  if (rest.length) {
    groups.push({
      key: "other",
      title: "Other optimizer calls",
      blurb: "Recorded, but not one of the stages this page knows by name.",
      calls: rest,
    });
  }
  return groups;
}

/** A label for one call inside its phase — the merge round, where there is one. */
export function callLabel(call, index, total) {
  if (call.level != null) return `round ${call.level}`;
  return total > 1 ? `call ${index + 1}` : "";
}
