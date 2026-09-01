import test from "node:test";
import assert from "node:assert/strict";
import { ACTIVATION_FLOOR, GROWTH_FACTOR, runWarnings } from "./optimize_warnings.js";

// The run overview's standing warnings. Every one of these describes a run that
// *looks* successful — the chart climbs, the gate accepts, a skill is
// downloadable — and is not. That is why they live on the overview rather than
// inside a detail page: by the time someone opens a detail page they already
// suspect something.

const run = (over = {}) => ({
  best_step: 2, overlap_item_keys: [], detector: {}, ...over,
});

const step = (no, over = {}) => ({
  step_no: no, gate_action: "accept", val_activation_rate: 1, skill_len: 1000,
  n_answer_leaks: 0, ...over,
});

const ids = (r, s) => runWarnings(r, s).map((w) => w.id);
const find = (r, s, id) => runWarnings(r, s).find((w) => w.id === id);

// --- Nothing to say ----------------------------------------------------------

test("a healthy run raises nothing", () => {
  // The list is only worth reading if it is usually empty. A warning that is
  // always present is a decoration, and the run that deserved one gets skimmed.
  assert.deepEqual(ids(run(), [step(0), step(1), step(2)]), []);
});

test("a run with no steps yet raises nothing rather than raising everything", () => {
  // Every rule reads a step field. Treating "not measured yet" as zero would
  // greet a run in its first minute with a low-activation warning and an
  // "unknown" banner, from data that has not arrived.
  assert.deepEqual(ids(run({ best_step: null }), []), []);
});

// --- Validation that is not held out ----------------------------------------

test("questions in both splits are reported, because the gate stops meaning anything", () => {
  const w = find(run({ overlap_item_keys: ["a:1", "b:2"] }), [step(0)], "overlap");
  assert.equal(w.tone, "warning");
  assert.match(w.body, /2 question/);
});

// --- The skill the agent never read -----------------------------------------

test("a skill the agent was rarely seen reading is reported against the step you would download", () => {
  // The failure: validation accuracy improves while the agent never opened the
  // skill, so the improvement came from somewhere else — sampling noise, a
  // judge drifting — and the edits get the credit. The number that matters is
  // the *best* step's, because that is the skill the download button hands out.
  const steps = [step(0), step(1, { val_activation_rate: 1 }), step(2, { val_activation_rate: 0.4 })];
  const w = find(run({ best_step: 2 }), steps, "activation-low");
  assert.equal(w.tone, "warning");
  assert.match(w.body, /40%/);
  assert.ok(ACTIVATION_FLOOR > 0.4);
});

test("a healthy best step is not condemned by an earlier bad one", () => {
  // Reading the worst step, or averaging, would fire on a run that had one bad
  // step and recovered — which is a normal shape for a run and not a problem.
  const steps = [step(0), step(1, { val_activation_rate: 0.2 }), step(2, { val_activation_rate: 1 })];
  assert.ok(!ids(run({ best_step: 2 }), steps).includes("activation-low"));
});

test("activation the detectors could not determine is its own message, not a low score", () => {
  // `null` means neither detector could tell, which the backend is careful to
  // distinguish from "no". Folding it into the low-activation warning would
  // accuse a run of something nobody measured.
  const w = find(run(), [step(0), step(1), step(2, { val_activation_rate: null })], "activation-unknown");
  assert.equal(w.tone, "info");
  assert.ok(!ids(run(), [step(2, { val_activation_rate: null })]).includes("activation-low"));
});

// --- A skill that grew ------------------------------------------------------

test("a skill that has grown several times over is reported", () => {
  // Nothing in the loop pushes back on length: every step may add lines and the
  // gate only asks whether accuracy went up. Runs drift toward a skill that is
  // mostly near-duplicate rules, which costs context on every single call the
  // agent will ever make and is invisible on the accuracy chart.
  const steps = [step(0, { skill_len: 1000 }), step(1), step(2, { skill_len: 3000 })];
  const w = find(run({ best_step: 2 }), steps, "skill-size");
  assert.equal(w.tone, "warning");
  assert.match(w.body, /3.0×/);
  assert.equal(GROWTH_FACTOR, 2);
});

test("ordinary growth is not reported", () => {
  const steps = [step(0, { skill_len: 1000 }), step(2, { skill_len: 1400 })];
  assert.ok(!ids(run({ best_step: 2 }), steps).includes("skill-size"));
});

test("a skill that shrank is not reported as having grown", () => {
  const steps = [step(0, { skill_len: 4000 }), step(2, { skill_len: 900 })];
  assert.ok(!ids(run({ best_step: 2 }), steps).includes("skill-size"));
});

test("growth is measured against the baseline, not against the previous step", () => {
  // A run that doubles the skill one modest step at a time never trips a
  // step-to-step comparison, and that is exactly the run worth catching.
  const steps = [
    step(0, { skill_len: 1000 }), step(1, { skill_len: 1600 }),
    step(2, { skill_len: 2400 }),
  ];
  assert.ok(ids(run({ best_step: 2 }), steps).includes("skill-size"));
});

// --- Memorised answers ------------------------------------------------------

test("a leak in a step the gate kept is an error, because it is in the skill", () => {
  // The one warning that is not about measurement being unreliable — here the
  // artefact itself is bad. Whoever downloads this skill deploys a lookup table
  // for questions the eval set happened to contain.
  const steps = [step(0), step(1, { gate_action: "accept_new_best", n_answer_leaks: 2 }), step(2)];
  const w = find(run(), steps, "answer-leak");
  assert.equal(w.tone, "error");
  assert.match(w.body, /step 1/);
});

test("a leak the gate happened to reject is reported more quietly, and separately", () => {
  // The text never reached the skill, so this is not a broken artefact — but an
  // optimizer that tried to memorise an answer once will try again, and the
  // gate catching it was luck rather than a defence.
  const steps = [step(0), step(1, { gate_action: "reject", n_answer_leaks: 1 })];
  const raised = runWarnings(run(), steps);
  assert.deepEqual(raised.map((w) => w.id), ["answer-leak-rejected"]);
  assert.equal(raised[0].tone, "warning");
});

test("leaks in kept and rejected steps are both reported, not merged", () => {
  // Merging them would force one tone on two different situations: either the
  // rejected case gets an error it does not deserve, or the accepted case gets
  // a warning that understates a skill nobody should deploy.
  const steps = [
    step(0),
    step(1, { gate_action: "reject", n_answer_leaks: 1 }),
    step(2, { gate_action: "accept_new_best", n_answer_leaks: 1 }),
  ];
  assert.deepEqual(ids(run(), steps).sort(), ["answer-leak", "answer-leak-rejected"]);
});

test("every leaking step is named, not just the first", () => {
  const steps = [
    step(1, { gate_action: "accept", n_answer_leaks: 1 }),
    step(2, { gate_action: "accept", n_answer_leaks: 3 }),
  ];
  assert.match(find(run(), steps, "answer-leak").body, /steps 1, 2/);
});

// --- The agent that moved underneath the run --------------------------------

test("a step that ran against a different agent config than the run pinned is reported", () => {
  // The failure this catches is invisible by construction: somebody deploys to
  // the agent server mid-run, the steps before and after measure two different
  // systems, and the only symptom is the accuracy moving — which is the thing
  // the chart exists to show. The gate then accepts or rejects a candidate for
  // a reason that has nothing to do with its edits.
  const steps = [
    step(0, { workspace_version: "cfg-1" }),
    step(1, { workspace_version: "cfg-1" }),
    step(2, { workspace_version: "cfg-2" }),
  ];
  const w = find(run({ workspace_version: "cfg-1" }), steps, "workspace-drift");
  assert.equal(w.tone, "warning");
  assert.match(w.body, /step 2/);
});

test("steps that never probed the agent are not accused of drifting", () => {
  // `null` is "the workspace seam was off, or the probe failed" — the backend
  // is careful to record that rather than an empty string. Treating it as a
  // mismatch would warn about drift on every run that never looked.
  const steps = [step(0, { workspace_version: null }), step(1, { workspace_version: null })];
  assert.deepEqual(ids(run({ workspace_version: "cfg-1" }), steps), []);
});

test("a run with no pinned version has nothing to compare against", () => {
  const steps = [step(1, { workspace_version: "cfg-9" })];
  assert.deepEqual(ids(run({ workspace_version: null }), steps), []);
});

test("every step that saw a different config is named, and the config is quoted", () => {
  // "Something changed" sends someone to the agent server's deploy log with no
  // idea what to look for. The version string is what they will search.
  const steps = [
    step(1, { workspace_version: "cfg-2" }),
    step(2, { workspace_version: "cfg-2" }),
  ];
  const w = find(run({ workspace_version: "cfg-1" }), steps, "workspace-drift");
  assert.match(w.body, /steps 1, 2/);
  assert.match(w.body, /cfg-1/);
});

// --- The pre-flight probe ----------------------------------------------------

test("a pre-flight that could not see the skill is carried through", () => {
  const w = find(
    run({ detector: { preflight: { ok: false, message: "no tool call touched billing/" } } }),
    [step(0)],
    "detector",
  );
  assert.equal(w.tone, "warning");
  assert.match(w.body, /billing\//);
});

test("a pre-flight that succeeded says nothing", () => {
  assert.deepEqual(ids(run({ detector: { preflight: { ok: true, message: "" } } }), [step(0)]), []);
});

// --- Routing: what the optimizer said, and what the measurement mixed --------
//
// Both of these describe a routing run that produces steps, draws a chart and
// completes — and whose numbers do not mean what the page implies. They are the
// same class as the drift warning above: a fact about the measurement, not
// about the skill.

test("an optimizer that says the descriptions are not the problem is quoted, not summarised", () => {
  // The symptom otherwise is a column of "0 edits applied" with the reason
  // buried in a minibatch's raw JSON, three clicks into a page nobody opens
  // while the chart is merely flat.
  const steps = [
    step(0),
    step(1, { routing_blocked_by: "the system prompt tells the agent to answer directly" }),
    step(2, { routing_blocked_by: "the system prompt tells the agent to answer directly" }),
  ];
  const w = find(run({ mode: "routing" }), steps, "routing-blocked");
  assert.equal(w.tone, "warning");
  assert.match(w.body, /answer directly/);
  assert.match(w.body, /steps 1, 2/);
});

test("a routing run nobody blocked says nothing", () => {
  assert.ok(!ids(run({ mode: "routing" }), [step(0), step(1)]).includes("routing-blocked"));
});

test("questions answered under genuinely different agent setups are reported", () => {
  // One routing accuracy over a batch that ran under two systems is an average
  // of two things, and the analyst read the same batch as though it were one.
  const steps = [
    step(0),
    step(1, { setup_divergence: { n_prompts: 100, n_variants: 2, majority_share: 0.6 } }),
  ];
  const w = find(run({ mode: "routing" }), steps, "setup-divergence");
  assert.equal(w.tone, "warning");
  assert.match(w.body, /2/);
});

test("a moved timestamp is not divergence and never reaches the overview", () => {
  // The backend only sets the field when the variants differ too much to show
  // as one prompt. Warning on every run whose clock ticks would train people to
  // ignore the warning that matters.
  const steps = [step(0), step(1, { setup_divergence: null })];
  assert.ok(!ids(run({ mode: "routing" }), steps).includes("setup-divergence"));
});

test("a routing run configured to withhold its successes is told the flag is ignored", () => {
  // `failure_only` drops the successes, which in routing are the constraint
  // that stops a description narrowing until it wins nothing. The engine
  // ignores it in this mode; a deployment that set it would otherwise believe
  // it took effect.
  const w = find(
    run({ mode: "routing", config: { failure_only: true } }),
    [step(0), step(1)],
    "routing-failure-only",
  );
  assert.equal(w.tone, "info");
});

test("an isolated run honours failure_only, so nothing is said about it", () => {
  assert.ok(
    !ids(run({ mode: "isolated", config: { failure_only: true } }), [step(0)])
      .includes("routing-failure-only"),
  );
});
