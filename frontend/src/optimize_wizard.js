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
// The fix for the first is structural rather than disciplinary: checks are held
// in a map keyed by skill name, and `checkFor` can only ever return the entry
// filed under the skill asked for. There is no reset to forget.

import { canStart } from "./optimize_split.js";

// Mode comes first, and the agent check now lives on the Skill step rather than
// on a Target step of its own.
//
// Mode first because it changes what the Skill step *means*. `isolated` sends
// one skill and edits its body; `routing` sends the whole workspace and edits
// one description, which a skill without frontmatter cannot offer. Asking for
// the skill before the mode meant the wizard could not say which skills were
// eligible at the moment it was asking — and then rejected the answer two steps
// later, on the old Target step, after the split had already been chosen.
//
// The agent check merged into Skill for the same reason: once the mode is known
// up front, every candidate skill can be checked as the step opens, so
// eligibility is part of picking rather than a verdict delivered afterwards. A
// separate step would have restated what the card already showed.
export const STEPS = [
  { id: "mode", label: "Mode", hint: "What this run edits" },
  { id: "source", label: "Source", hint: "Which eval sets" },
  { id: "skill", label: "Skill", hint: "What to optimise" },
  { id: "split", label: "Split", hint: "Train and validate" },
  { id: "settings", label: "Settings", hint: "Models and grading" },
  { id: "review", label: "Review", hint: "Start" },
];

// The agent check for one skill, or nothing. `checks` is `{ [skillName]: entry }`
// — a check for a different skill is not stale data to be cleaned up later, it
// is simply filed elsewhere and unreachable from here.
export function checkFor(checks, skill) {
  if (!checks || !skill) return null;
  return checks[skill] || null;
}

// Whether one skill can be optimised in this mode, and what to say when it
// cannot. Read by three callers that used to answer it separately: the footer's
// blocking sentence, the card's disabled state, and the default selection.
//
// `state` is one of:
//   checking — the agent has not answered yet
//   failed   — the request did not get through; says nothing about the skill
//   blocked  — the agent answered and this skill cannot be used in this mode
//   ready    — usable
export function skillStatus(check, mode) {
  if (!check || check.status === "checking") {
    return { state: "checking", reason: "Checking the agent…" };
  }
  // Never the same sentence as "in progress". A check that failed is not going
  // to arrive, and the screen offers a retry beside this.
  if (check.status === "failed") {
    return {
      state: "failed",
      reason: `The agent could not be checked: ${check.error || "the request failed"}`,
    };
  }
  const result = check.result || {};
  if (!result.exists) {
    return {
      state: "blocked",
      reason: `The agent has no skill directory named “${check.skill}”.`,
    };
  }
  if (mode === "routing" && !result.has_frontmatter) {
    return {
      state: "blocked",
      reason: result.routing_blocked_reason || "This skill has no frontmatter to edit.",
    };
  }
  return { state: "ready", reason: null };
}

// Which skill to select for someone who has not chosen one.
//
// The first usable skill, falling back to the first skill at all. The fallback
// matters while the checks are still in flight: selecting nothing until every
// agent call has returned would leave the step looking like the old one — a
// wall of tables with no indication that picking is what it wants — for exactly
// as long as the slowest request takes. Selecting the first immediately and
// moving off it if it turns out to be ineligible is the behaviour that reads as
// "already filled in" rather than "still loading".
export function defaultSkill(groups, checks, mode) {
  const names = (groups || []).map((g) => g.skill_name);
  if (!names.length) return null;
  const usable = names.find((name) => skillStatus(checkFor(checks, name), mode).state === "ready");
  return usable ?? names[0];
}

// --- Hyperparameters --------------------------------------------------------

// Held as raw strings while being typed, because a controlled number input
// backed by `Number(raw)` cannot be cleared: emptying it yields `Number("")`,
// which is 0, which the input immediately renders back. Select-all-and-retype —
// the way anyone edits a number — was impossible, and 0 reached the API.
// `concurrency` is here with the training numbers rather than among the
// connection settings because it is validated the same way and typed on the
// same screen — but note it is not a hyperparameter: it changes how fast the
// run goes, never what it produces. The cap is this side's alone; the server
// only insists on ≥ 1. Above about this many parallel questions the limit stops
// being the optimizer and starts being the agent server the developer is
// pointing at, and a run that overruns it fails as "the agent is flaky".
export const HYPER_FIELDS = {
  num_epochs: { min: 1, max: 20 },
  batch_size: { min: 1 },
  learning_rate: { min: 1 },
  concurrency: { min: 1, max: 32 },
  // How much trajectory text one analyst prompt may carry. Also not a
  // hyperparameter — it changes how much evidence the analyst sees, never the
  // algorithm — but it is the one setting that decides whether the call fits in
  // the optimizer model's context window at all, which is why it is on the form
  // rather than in the API only. The floor matches the API's own.
  reflect_budget_chars: { min: 1000 },
};

// A character count read as tokens, which is the unit a context window is sold
// in. Deliberately a range rather than a figure: the ratio depends on the text,
// and the two ends here are the ones that actually bracket what goes into these
// prompts — dense JSON tool results at the low end, ordinary prose at the high
// end. A single number would be quoted back as if it were measured.
//
// CJK is outside the range and the caller says so separately: a Chinese
// character is often a token by itself, so the same budget can cost several
// times what this estimate suggests.
export function tokenEstimate(chars) {
  if (!Number.isFinite(chars) || chars <= 0) return null;
  return { low: Math.round(chars / 4), high: Math.round(chars / 2.5) };
}

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

// What goes in the request body's `config`. Everything in `HYPER_FIELDS` is sent
// by `start()` from its validated value, so it must not be duplicated here — the
// raw string beside the parsed number is how "1x" reached the API.
export function extraConfig(hyper) {
  const rest = {};
  for (const [key, value] of Object.entries(hyper || {})) {
    if (!(key in HYPER_FIELDS)) rest[key] = value;
  }
  return rest;
}

// What is actually sent as the run's `config`.
//
// A blank field means "use the server's environment", which the API expresses as
// the field being absent — every one of them has a default. An empty *string* is
// not the same thing: `agent_timeout_s` is typed `float | None`, so a developer
// who typed a timeout and then cleared the box was sending `""` and getting a
// 422 from a field they had deliberately emptied. Dropped here rather than
// guarded per field, because the next number added to this form would have
// arrived with the same bug.
export function cleanConfig(config) {
  const out = {};
  for (const [key, value] of Object.entries(config || {})) {
    if (value === null || value === undefined) continue;
    if (typeof value === "string" && value.trim() === "") continue;
    out[key] = value;
  }
  return out;
}

// --- Gating -----------------------------------------------------------------

// What stops the given step from being left. `null` means nothing does.
export function blockingReason(state) {
  const { stepIndex } = state;
  const id = STEPS[stepIndex]?.id;

  // `mode` asks nothing: it opens with `isolated` already chosen, and both
  // modes are always offerable because what makes `routing` impossible is a
  // property of a skill, which has not been picked yet.
  if (id === "mode") return null;

  // The questions load themselves as soon as a set is ticked, so nothing here
  // asks for an action any more — it reports. It used to say "Load the questions
  // to continue", which was a footer sentence describing a button three inches
  // above it: the wizard knew what it needed, could fetch it unprompted, and
  // instead blocked until the developer pressed a key it had already told them
  // to press.
  if (id === "source") {
    if (!state.sourceIds?.length) return "Choose at least one eval set.";
    if (state.previewError) return "The questions could not be loaded — try again.";
    if (!state.preview) return "Reading the questions…";
    return null;
  }

  // Picking the skill and clearing it against the agent are the same step now,
  // so this is where a missing skill, a missing directory, an unreachable agent
  // and a mode the skill cannot serve are all reported.
  if (id === "skill") {
    if (!state.skill) return "Pick the skill this run optimises.";
    return skillStatus(checkFor(state.checks, state.skill), state.mode).reason;
  }

  if (id === "split") {
    if (!state.split) return "Pick a skill first.";
    if (!canStart(state.split, state.limits || {})) {
      return "The split is too small — see above.";
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

// The seams whose being fake changes what a run *means*, in the order the
// review banner names them. Trace and workspace are left out on purpose: a fake
// trace still produces a scored run, while a fake agent, judge or optimizer each
// replace one of the three things a run is made of.
export const RUN_SEAMS = ["agent", "judge", "optimizer"];

// Which of them are fake, for the review step's banner.
//
// The banner used to be keyed on the agent alone, so a stack with a real agent
// and a real judge but `OPTIMIZER_IMPL=fake` said nothing at all — that run
// spends real money on real rollouts and then writes canned skill edits, which
// is the most expensive way to be confused about a switch.
export function fakeSeams(impls) {
  return RUN_SEAMS.filter((seam) => (impls || {})[seam] === "fake");
}
