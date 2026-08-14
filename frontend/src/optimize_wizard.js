// The new-run wizard's gating: which step you may be on, what is stopping you,
// and whether the numbers at the end are numbers.
//
// Pulled out of the component because all three were wrong in ways a render
// function cannot be tested for. The wizard asks for real money at the end, and
// each of the bugs below let it be asked for under a false premise:
//
//   * The skill check was never re-run when the skill changed. `chooseSkill`
//     cleared the split but not the check, and the only thing that triggered a
//     re-check was `!check` — so once any check had run, picking a different
//     skill kept the first one's file list, character count and frontmatter
//     flag. A run could be started in `routing` mode against a skill that has
//     no frontmatter, because a different skill did.
//
//   * `furthest` returned 5 the moment a check existed, regardless of whether
//     it succeeded or what it was for. After going back and clearing the skill,
//     every step was reachable — and the split step renders nothing without a
//     split, so the wizard showed a blank body under a footer that said "Pick a
//     skill first."
//
//   * `blockingReason` said "Checking the agent…" for any absent check,
//     including one that had failed and would never arrive.
//
// The fix for the first is structural rather than disciplinary: the check
// carries the skill it was run for, and `checkFor` refuses to hand it back for
// any other. There is no reset to forget.

import { canStart } from "./optimize_split.js";

export const STEPS = [
  { id: "source", label: "Source", hint: "Which eval sets" },
  { id: "skill", label: "Skill", hint: "What to optimise" },
  { id: "split", label: "Split", hint: "Train and validate" },
  { id: "target", label: "Target", hint: "The agent" },
  { id: "settings", label: "Settings", hint: "Models and grading" },
  { id: "review", label: "Review", hint: "Start" },
];

// The skill check, or nothing, for the skill actually selected. A check for a
// different skill is not stale data to be cleaned up later — from here it
// simply does not exist.
export function checkFor(check, skill) {
  if (!check || !skill || check.skill !== skill) return null;
  return check;
}

// --- Hyperparameters --------------------------------------------------------

// Held as raw strings while being typed, because a controlled number input
// backed by `Number(raw)` cannot be cleared: emptying it yields `Number("")`,
// which is 0, which the input immediately renders back. Select-all-and-retype —
// the way anyone edits a number — was impossible, and 0 reached the API.
export const HYPER_FIELDS = {
  num_epochs: { min: 1, max: 20 },
  batch_size: { min: 1 },
  learning_rate: { min: 1 },
};

export function parseCount(raw, { min, max } = {}) {
  const text = String(raw ?? "").trim();
  if (text === "") return { value: null, error: "Required." };
  if (!/^\d+$/.test(text)) return { value: null, error: "Whole numbers only." };
  const value = Number(text);
  if (min != null && value < min) return { value: null, error: `Must be at least ${min}.` };
  if (max != null && value > max) return { value: null, error: `Must be at most ${max}.` };
  return { value, error: null };
}

// Every hyperparameter at once: the effective value of each, the message for
// each that is wrong, and whether the run may be started. A field the user has
// not touched falls back to the server's default and is never an error — the
// wizard is startable without opening this section at all.
export function hyperState(hyper, defaults) {
  const values = {};
  const errors = {};
  for (const [key, spec] of Object.entries(HYPER_FIELDS)) {
    const raw = hyper?.[key];
    if (raw === undefined || raw === null) {
      values[key] = defaults?.[key] ?? null;
      continue;
    }
    const { value, error } = parseCount(raw, spec);
    if (error) errors[key] = error;
    else values[key] = value;
  }
  return { values, errors, ok: Object.keys(errors).length === 0 };
}

// What goes in the request body's `config`. The three named above are sent as
// their own fields, so they must not be duplicated here.
export function extraConfig(hyper) {
  const rest = {};
  for (const [key, value] of Object.entries(hyper || {})) {
    if (!(key in HYPER_FIELDS)) rest[key] = value;
  }
  return rest;
}

// --- Gating -----------------------------------------------------------------

// What stops the given step from being left. `null` means nothing does.
export function blockingReason(state) {
  const { stepIndex } = state;
  const id = STEPS[stepIndex]?.id;

  if (id === "source") {
    if (!state.sourceIds?.length) return "Choose at least one eval set.";
    if (!state.preview) return "Load the questions to continue.";
    return null;
  }

  if (id === "skill") {
    return state.skill ? null : "Pick the skill this run optimises.";
  }

  if (id === "split") {
    if (!state.split) return "Pick a skill first.";
    if (!canStart(state.split, state.limits || {})) {
      return "The split is too small — see above.";
    }
    return null;
  }

  if (id === "target") {
    const check = checkFor(state.check, state.skill);
    if (!check || check.status === "checking") return "Checking the agent…";
    // Never the same sentence as "in progress". A check that failed is not
    // going to arrive, and the screen offers a retry beside this.
    if (check.status === "failed") {
      return `The agent could not be checked: ${check.error || "the request failed"}`;
    }
    if (!check.result?.exists) {
      return `The agent has no skill directory named “${state.skill}”.`;
    }
    if (state.mode === "routing" && !check.result.has_frontmatter) {
      return check.result.routing_blocked_reason || "This skill has no frontmatter to edit.";
    }
    return null;
  }

  if (id === "review") {
    const { errors } = hyperState(state.hyper, state.defaults);
    const first = Object.values(errors)[0];
    if (first) return `Check the training settings: ${first.toLowerCase()}`;
    return null;
  }

  // `settings` asks for nothing — every field on it falls back to the server's
  // own environment, which is what makes the fake demo runnable untouched.
  return null;
}

// The furthest step the step bar may offer, derived rather than tracked: you
// can reach step N only if every step before it is satisfied. One definition,
// so the bar and the Continue button can never disagree about whether the run
// is ready — which is how the old version offered a step that rendered blank.
export function furthestStep(state) {
  for (let i = 0; i < STEPS.length; i += 1) {
    if (blockingReason({ ...state, stepIndex: i })) return i;
  }
  return STEPS.length - 1;
}
