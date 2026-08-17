import test from "node:test";
import assert from "node:assert/strict";

import { makeSplit } from "./optimize_split.js";
import {
  STEPS,
  blockingReason,
  checkFor,
  cleanConfig,
  defaultSkill,
  extraConfig,
  fakeSeams,
  furthestStep,
  hyperState,
  parseCount,
  skillStatus,
  tokenEstimate,
} from "./optimize_wizard.js";

// Every case below is one where the wizard let a run be started, or a step be
// opened, under a premise that was not true. This screen spends an hour of
// agent calls on whatever it is told, so "it looked fine" is the failure mode
// that matters.

const index = (id) => STEPS.findIndex((s) => s.id === id);

const question = (key, over = {}) => ({
  item_key: key,
  question: `q${key}`,
  eval_set_name: "set",
  prior_accuracy: 0.5,
  ...over,
});

const splitOf = (nTrain, nVal) => {
  const questions = [];
  const train = [];
  const val = [];
  for (let i = 0; i < nTrain; i += 1) {
    questions.push(question(`t${i}`));
    train.push(`t${i}`);
  }
  for (let i = 0; i < nVal; i += 1) {
    questions.push(question(`v${i}`));
    val.push(`v${i}`);
  }
  return makeSplit(questions, { train, val });
};

const okCheck = (skill, over = {}) => ({
  skill,
  status: "ok",
  result: { exists: true, has_frontmatter: true, files: ["SKILL.md"], n_chars: 10, ...over },
});

const checksOf = (...entries) =>
  Object.fromEntries(entries.map((entry) => [entry.skill, entry]));

const ready = (over = {}) => ({
  sourceIds: ["a"],
  preview: { groups: [] },
  skill: "writer",
  split: splitOf(20, 10),
  limits: {},
  checks: checksOf(okCheck("writer")),
  mode: "isolated",
  hyper: {},
  defaults: { num_epochs: 3, batch_size: 4, learning_rate: 2 },
  ...over,
});

// --- The stale check --------------------------------------------------------

test("a check belongs to the skill it was run for, and to no other", () => {
  const check = okCheck("writer");
  const checks = checksOf(check);
  assert.equal(checkFor(checks, "writer"), check);
  assert.equal(checkFor(checks, "router"), null);
  assert.equal(checkFor(checks, null), null);
  assert.equal(checkFor(null, "writer"), null);
});

test("changing the skill cannot inherit the previous skill's check", () => {
  // The bug: pick A, check runs, go back, pick B — and the wizard showed A's
  // files while the footer validated B against A's frontmatter flag.
  const state = ready({ skill: "router", checks: checksOf(okCheck("writer")) });
  assert.match(blockingReason({ ...state, stepIndex: index("skill") }), /Checking the agent/);
});

test("routing mode is blocked by the selected skill's own frontmatter", () => {
  const blocked = ready({
    mode: "routing",
    checks: checksOf(
      okCheck("writer", {
        has_frontmatter: false,
        routing_blocked_reason: "SKILL.md has no frontmatter block.",
      }),
    ),
  });
  assert.equal(
    blockingReason({ ...blocked, stepIndex: index("skill") }),
    "SKILL.md has no frontmatter block.",
  );
  // The same run in isolated mode is fine — the modes freeze opposite halves.
  assert.equal(blockingReason({ ...blocked, mode: "isolated", stepIndex: index("skill") }), null);
});

// --- The check state machine ------------------------------------------------

test("a failed check does not masquerade as one still running", () => {
  const failed = ready({
    checks: checksOf({ skill: "writer", status: "failed", error: "connection refused" }),
  });
  const reason = blockingReason({ ...failed, stepIndex: index("skill") });
  assert.match(reason, /could not be checked/);
  assert.match(reason, /connection refused/);
  assert.ok(!reason.includes("Checking"), reason);
});

test("a check that is genuinely in flight says so", () => {
  const checking = ready({ checks: checksOf({ skill: "writer", status: "checking" }) });
  assert.match(blockingReason({ ...checking, stepIndex: index("skill") }), /Checking the agent/);
});

test("a skill missing from the agent is named, not merely rejected", () => {
  const missing = ready({
    checks: checksOf({ skill: "writer", status: "ok", result: { exists: false } }),
  });
  assert.match(blockingReason({ ...missing, stepIndex: index("skill") }), /writer/);
});

// --- Mode first -------------------------------------------------------------

test("the mode step comes before the source step and blocks nothing", () => {
  assert.equal(index("mode"), 0);
  assert.ok(index("mode") < index("source"));
  assert.ok(index("source") < index("skill"));
  // Openable from a standing start: the wizard mounts on it with `isolated`
  // already chosen, so there is nothing to satisfy.
  assert.equal(blockingReason({ stepIndex: index("mode"), mode: "isolated" }), null);
  assert.equal(blockingReason({ stepIndex: index("mode"), mode: "routing" }), null);
});

test("the wizard opens on a step that is immediately usable", () => {
  // The old first step was Source, which blocked until an eval set was ticked.
  // Whatever is first must not greet an empty wizard with a disabled Continue
  // and no explanation of what it wants.
  assert.equal(furthestStep({ mode: "isolated", hyper: {}, defaults: {} }), index("source"));
});

// --- Eligibility, shared by the footer, the cards and the default ------------

test("skillStatus separates cannot-be-checked from cannot-be-used", () => {
  assert.equal(skillStatus(null, "isolated").state, "checking");
  assert.equal(skillStatus({ skill: "w", status: "checking" }, "isolated").state, "checking");
  assert.equal(
    skillStatus({ skill: "w", status: "failed", error: "boom" }, "isolated").state,
    "failed",
  );
  assert.equal(
    skillStatus({ skill: "w", status: "ok", result: { exists: false } }, "isolated").state,
    "blocked",
  );
  assert.equal(skillStatus(okCheck("w"), "isolated").state, "ready");
});

test("a skill without frontmatter is ready for isolated and blocked for routing", () => {
  const check = okCheck("w", { has_frontmatter: false });
  assert.equal(skillStatus(check, "isolated").state, "ready");
  assert.equal(skillStatus(check, "isolated").reason, null);
  assert.equal(skillStatus(check, "routing").state, "blocked");
  assert.match(skillStatus(check, "routing").reason, /frontmatter/);
});

// --- The default selection --------------------------------------------------

test("the first skill is selected before any check has come back", () => {
  // Waiting for every agent call would leave the step looking exactly like the
  // old one — a wall of tables with nothing chosen — for as long as the slowest
  // request takes.
  const groups = [{ skill_name: "billing" }, { skill_name: "reporting" }];
  assert.equal(defaultSkill(groups, {}, "isolated"), "billing");
});

test("routing skips a skill it cannot edit and takes the first that it can", () => {
  const groups = [{ skill_name: "billing" }, { skill_name: "reporting" }];
  const checks = checksOf(
    okCheck("billing", { has_frontmatter: false }),
    okCheck("reporting"),
  );
  assert.equal(defaultSkill(groups, checks, "routing"), "reporting");
  // The same pair in isolated mode keeps the first: frontmatter is irrelevant
  // there, and reordering the default would be unexplained.
  assert.equal(defaultSkill(groups, checks, "isolated"), "billing");
});

test("with nothing usable the default still names a skill, so the reason can be shown", () => {
  // Selecting nothing would put the wizard back on "Pick the skill this run
  // optimises." — which is not what is wrong. Something has to be selected for
  // its blocking reason to be the sentence in the footer.
  const groups = [{ skill_name: "billing" }];
  const checks = checksOf(okCheck("billing", { has_frontmatter: false }));
  assert.equal(defaultSkill(groups, checks, "routing"), "billing");
});

test("no groups means no default", () => {
  assert.equal(defaultSkill([], {}, "isolated"), null);
  assert.equal(defaultSkill(undefined, {}, "isolated"), null);
});

// --- Reachability -----------------------------------------------------------

test("reachability follows the prerequisite chain, one step at a time", () => {
  assert.equal(furthestStep({ sourceIds: [], hyper: {}, defaults: {} }), index("source"));
  assert.equal(furthestStep({ sourceIds: ["a"], hyper: {}, defaults: {} }), index("source"));

  const loaded = { sourceIds: ["a"], preview: { groups: [] }, hyper: {}, defaults: {} };
  assert.equal(furthestStep(loaded), index("skill"));

  // A skill picked but not yet cleared by the agent stops here — the check is
  // part of this step now, not of a later one.
  const picking = { ...loaded, skill: "writer", split: splitOf(20, 10), limits: {} };
  assert.equal(furthestStep(picking), index("skill"));

  assert.equal(furthestStep(ready()), index("review"));
});

test("a check for the wrong skill does not unlock the rest of the wizard", () => {
  // This is what returned 5 the moment any check existed.
  const state = ready({ skill: "router", checks: checksOf(okCheck("writer")) });
  assert.equal(furthestStep(state), index("skill"));
});

test("clearing the skill walks reachability back rather than leaving a blank step", () => {
  // Reload the preview and the skill and split go with it. The old wizard kept
  // `check`, so every step stayed reachable and the split step rendered an
  // empty body under "Pick a skill first."
  const state = ready({ skill: null, split: null });
  assert.equal(furthestStep(state), index("skill"));
});

test("an unstartable split stops the wizard at the split step", () => {
  const tiny = ready({ split: splitOf(1, 0), limits: { min_train: 4, min_val: 2 } });
  assert.equal(furthestStep(tiny), index("split"));
  assert.ok(blockingReason({ ...tiny, stepIndex: index("split") }));
});

test("the step bar and the Continue button share one definition of ready", () => {
  // furthestStep is defined as "every earlier step is unblocked", so there is
  // no state where the bar offers a step the button would refuse to reach.
  const state = ready();
  for (let i = 0; i <= furthestStep(state); i += 1) {
    if (i === furthestStep(state)) break;
    assert.equal(blockingReason({ ...state, stepIndex: i }), null, `step ${i} should be clear`);
  }
});

// --- Numbers ----------------------------------------------------------------

test("an emptied number field is an error, not a zero", () => {
  // `Number("")` is 0, which the controlled input rendered straight back, so
  // the field could not be cleared and 0 reached createOptimizationRun.
  assert.deepEqual(parseCount("", { min: 1 }), { value: null, error: "Required." });
});

test("a number field rejects what is not a whole count", () => {
  assert.match(parseCount("2.5", { min: 1 }).error, /Whole numbers/);
  assert.match(parseCount("abc", { min: 1 }).error, /Whole numbers/);
  assert.match(parseCount("-3", { min: 1 }).error, /Whole numbers/);
});

test("a number field enforces its own bounds", () => {
  assert.match(parseCount("0", { min: 1 }).error, /at least 1/);
  assert.match(parseCount("99", { min: 1, max: 20 }).error, /at most 20/);
  assert.deepEqual(parseCount("7", { min: 1, max: 20 }), { value: 7, error: null });
});

test("untouched fields take the server's defaults and are never errors", () => {
  const state = hyperState({}, { num_epochs: 3, batch_size: 4, learning_rate: 2 });
  assert.equal(state.ok, true);
  assert.equal(state.values.num_epochs, 3);
  assert.equal(state.values.batch_size, 4);
});

test("one bad field is reported without discarding the good ones", () => {
  const state = hyperState({ num_epochs: "", batch_size: "8" }, { learning_rate: 2 });
  assert.equal(state.ok, false);
  assert.ok(state.errors.num_epochs);
  assert.equal(state.values.batch_size, 8);
  assert.equal(state.values.learning_rate, 2);
});

test("the run cannot be started while a training number is invalid", () => {
  // The last gate. `start()` used to send whatever `Number(raw)` produced.
  const bad = ready({ hyper: { num_epochs: "0" } });
  assert.ok(blockingReason({ ...bad, stepIndex: index("review") }));
  assert.equal(furthestStep(bad), index("review"));

  const good = ready({ hyper: { num_epochs: "4" } });
  assert.equal(blockingReason({ ...good, stepIndex: index("review") }), null);
});

test("a validated field is not duplicated into config as its raw string", () => {
  const extra = extraConfig({
    num_epochs: "3", batch_size: "4", learning_rate: "2", concurrency: "8", seed: "42",
  });
  assert.deepEqual(extra, { seed: "42" });
});

test("concurrency is validated, and out of range stops the run", () => {
  // How many questions go to the agent server at once. Not a hyperparameter —
  // it changes how fast the run is, never what it produces — but it is typed on
  // the same screen and wrong in the same ways, so it is checked the same way.
  assert.equal(hyperState({ concurrency: "4" }, {}).values.concurrency, 4);
  assert.match(hyperState({ concurrency: "0" }, {}).errors.concurrency, /at least 1/);
  assert.match(hyperState({ concurrency: "200" }, {}).errors.concurrency, /at most 32/);

  const bad = ready({ hyper: { concurrency: "0" } });
  assert.ok(blockingReason({ ...bad, stepIndex: index("review") }));
});

test("an untouched concurrency is the server's own, not a number this page invented", () => {
  const state = hyperState({}, { concurrency: 3 });
  assert.equal(state.ok, true);
  assert.equal(state.values.concurrency, 3);
});

// --- The source step loads itself -------------------------------------------

test("the source step reports rather than asking for a button press", () => {
  // The questions are fetched as soon as a set is ticked. The footer used to
  // read "Load the questions to continue", which described a control the wizard
  // could have pressed itself — and did not, so the step blocked on it.
  const picked = { stepIndex: index("source"), sourceIds: ["a"], hyper: {}, defaults: {} };
  assert.equal(blockingReason(picked), "Reading the questions…");
  assert.equal(blockingReason({ ...picked, preview: { groups: [] } }), null);
});

test("a failed load says so instead of claiming to still be reading", () => {
  // Without this the footer promises a fetch that is not coming, which is the
  // same bug the skill check had: "checking…" for a request that already failed.
  const failed = {
    stepIndex: index("source"), sourceIds: ["a"], previewError: "network down",
    hyper: {}, defaults: {},
  };
  assert.match(blockingReason(failed), /could not be loaded/);
});

test("choosing nothing is still the developer's move to make", () => {
  const empty = { stepIndex: index("source"), sourceIds: [], hyper: {}, defaults: {} };
  assert.equal(blockingReason(empty), "Choose at least one eval set.");
});

test("a field the developer cleared is absent, not an empty string", () => {
  // `agent_timeout_s` is `float | None` on the API. Typing a timeout and then
  // thinking better of it left "" in the box, which is a 422 rather than the
  // "use the environment" that every blank field on this form means.
  const sent = cleanConfig({
    agent_base_url: "http://agent:8080",
    agent_timeout_s: "",
    langfuse_host: "   ",
    judge_model: null,
    optimizer_model: undefined,
    concurrency: 4,
    failure_only: false,
  });
  assert.deepEqual(sent, {
    agent_base_url: "http://agent:8080",
    // A real false is a value, not a blank — dropping it would silently turn
    // "off" into "whatever the server defaults to".
    concurrency: 4,
    failure_only: false,
  });
});

test("cleaning a config nobody filled in is an empty object, not a crash", () => {
  assert.deepEqual(cleanConfig({}), {});
  assert.deepEqual(cleanConfig(undefined), {});
});

// --- The trajectory budget --------------------------------------------------
//
// It decides whether an analyst call fits in the optimizer's context window at
// all, and it used to be reachable only through the API — so "the model refused
// the request" was a thing you could hit and not adjust from anywhere you could
// see.

test("the trajectory budget is sent with the run's config", () => {
  const { values, errors, ok } = hyperState(
    { reflect_budget_chars: "120000" },
    { reflect_budget_chars: 200000 },
  );

  assert.equal(ok, true);
  assert.deepEqual(errors, {});
  assert.equal(values.reflect_budget_chars, 120000);
});

test("an untouched budget falls back to the server's default", () => {
  const { values } = hyperState({}, { reflect_budget_chars: 200000 });

  assert.equal(values.reflect_budget_chars, 200000);
});

test("a budget below the API's floor is refused here rather than by a 422", () => {
  const { errors, ok } = hyperState({ reflect_budget_chars: "500" }, {});

  assert.equal(ok, false);
  assert.match(errors.reflect_budget_chars, /at least 1000/);
});

test("the token estimate is a range, because the ratio depends on the text", () => {
  const est = tokenEstimate(200000);

  assert.equal(est.low, 50000);
  assert.equal(est.high, 80000);
  assert.ok(est.low < est.high);
});

test("a budget mid-edit has no estimate rather than an estimate of zero", () => {
  assert.equal(tokenEstimate(null), null);
  assert.equal(tokenEstimate(0), null);
  assert.equal(tokenEstimate(NaN), null);
});

// The review banner's whole job is to say "part of this is not real". It was
// keyed on the agent seam alone, which is the one combination where the run is
// obviously fake anyway; the combination it stayed silent on — real rollouts,
// canned edits — is the one that costs money and proves nothing.
test("a fake optimizer counts even when the agent and judge are real", () => {
  assert.deepEqual(
    fakeSeams({ agent: "real", judge: "real", optimizer: "fake", trace: "fake" }),
    ["optimizer"],
  );
});

test("an all-real run has nothing to warn about", () => {
  assert.deepEqual(fakeSeams({ agent: "real", judge: "real", optimizer: "real" }), []);
});

test("seams are named in a fixed order, whatever order they arrived in", () => {
  assert.deepEqual(
    fakeSeams({ optimizer: "fake", judge: "fake", agent: "fake" }),
    ["agent", "judge", "optimizer"],
  );
});

test("a config that has not loaded yet warns about nothing", () => {
  assert.deepEqual(fakeSeams(undefined), []);
});
