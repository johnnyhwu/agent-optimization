import test from "node:test";
import assert from "node:assert/strict";
import {
  MIN_QUESTIONS_PER_SKILL,
  SMALL_VAL_SPLIT,
  SUGGESTED_QUESTIONS_PER_SKILL,
  routingReviewWarnings,
  routingSkillWarnings,
  suggestedBatchSize,
} from "./optimize_routing_warnings.js";

// Configuration a routing run cannot recover from, caught before it is paid for.
//
// Every rule here is about the same asymmetry: a routing run rewrites a
// three-sentence description from whatever evidence its batch happened to hold,
// and several ways of setting the wizard up make that evidence unusable while
// leaving the run looking entirely normal — the chart draws, the gate compares,
// steps complete. The cost of finding out afterwards is an hour of paid agent
// calls; the cost of finding out here is a banner.

const q = (key, skills) => ({ item_key: key, skills });

const previewOf = (groups) => ({
  groups: Object.entries(groups).map(([skill_name, questions]) => ({
    skill_name,
    questions,
  })),
});

const splitOf = (questions, { train, val } = {}) => ({
  questions,
  byKey: new Map(questions.map((entry) => [entry.item_key, entry])),
  train: train ?? questions.map((entry) => entry.item_key),
  val: val ?? [],
});

const ids = (list) => list.map((w) => w.id);

// --- Skill selection ---------------------------------------------------------

test("a question tagged for a skill nobody selected is an error, not a nuisance", () => {
  // `gt_skills` is the question's own tags, unfiltered by what was ticked, and
  // routing scores an exact set match. So this question's only correct outcome
  // is "opened billing AND reporting" while reporting's description is frozen:
  // it can never score correct, it drags every step, and it pushes the analyst
  // to over-narrow the one description it *is* allowed to touch.
  const preview = previewOf({
    billing: [q("a", ["billing"]), q("b", ["billing", "reporting"])],
  });
  const w = routingSkillWarnings({ mode: "routing", skills: ["billing"], preview });
  assert.deepEqual(ids(w), ["unselected-tags"]);
  assert.equal(w[0].tone, "error");
  assert.match(w[0].body, /reporting/);
});

test("selecting both sides of the boundary silences it", () => {
  const preview = previewOf({
    billing: [q("b", ["billing", "reporting"])],
    reporting: [q("b", ["billing", "reporting"])],
  });
  assert.deepEqual(
    ids(routingSkillWarnings({ mode: "routing", skills: ["billing", "reporting"], preview })),
    [],
  );
});

test("a selected skill with no questions is reported, because it will be edited anyway", () => {
  // It is shown to the analyst in full and its description is rewritable, so a
  // skill with no evidence is not simply ignored — it is edited from the
  // evidence of the skills that did turn up, and then scored.
  const preview = previewOf({ billing: [q("a", ["billing"])], orphan: [] });
  const w = routingSkillWarnings({
    mode: "routing", skills: ["billing", "orphan"], preview,
  });
  assert.deepEqual(ids(w), ["skill-without-questions"]);
  assert.equal(w[0].tone, "warning");
  assert.match(w[0].body, /orphan/);
});

test("isolated raises none of it", () => {
  // Isolated sends one skill to the agent and edits its body. It does not score
  // a set match, so an unselected tag costs it nothing.
  const preview = previewOf({
    billing: [q("b", ["billing", "reporting"])],
  });
  assert.deepEqual(
    ids(routingSkillWarnings({ mode: "isolated", skills: ["billing"], preview })),
    [],
  );
});

// --- The review step ---------------------------------------------------------

const review = (over = {}) =>
  routingReviewWarnings({
    mode: "routing",
    skills: ["billing", "reporting"],
    split: splitOf([
      ...Array.from({ length: 20 }, (_, i) => q(`b${i}`, ["billing"])),
      ...Array.from({ length: 20 }, (_, i) => q(`r${i}`, ["reporting"])),
    ], { train: undefined, val: Array.from({ length: 40 }, (_, i) => `v${i}`) }),
    values: { learning_rate: 8, batch_size: 40, gate_metric: "soft" },
    ...over,
  });

test("an edit budget below the number of skills is reported", () => {
  // One description is one edit, and moving a boundary takes two — one on each
  // side. Ranking clips the pool to the budget, so a budget of one applies half
  // of a paired edit: the narrowing lands, the widening does not, and a class of
  // questions ends up claimed by nobody.
  const w = review({ values: { learning_rate: 1, batch_size: 40, gate_metric: "soft" } });
  assert.ok(ids(w).includes("edit-budget"));
  assert.match(w.find((x) => x.id === "edit-budget").body, /2/);
});

test("an edit budget matching the skill count is fine", () => {
  const w = review({ values: { learning_rate: 2, batch_size: 40, gate_metric: "soft" } });
  assert.ok(!ids(w).includes("edit-budget"));
});

test("too few questions per skill in a step is reported", () => {
  // Two questions per skill means one failure is half of that skill's evidence,
  // which is the variance that makes a routing run oscillate.
  const w = review({ values: { learning_rate: 8, batch_size: 4, gate_metric: "soft" } });
  const found = w.find((x) => x.id === "thin-batch");
  assert.equal(found.tone, "warning");
  assert.ok(MIN_QUESTIONS_PER_SKILL > 2);
});

test("a batch below the suggestion is offered a number rather than just a complaint", () => {
  const w = review({ values: { learning_rate: 8, batch_size: 12, gate_metric: "soft" } });
  const found = w.find((x) => x.id === "batch-suggestion");
  assert.equal(found.tone, "info");
  assert.equal(found.suggestion, 16);
  assert.match(found.body, /16/);
});

test("the suggestion never exceeds the training split", () => {
  // 8 per skill is what the evidence wants; the split is what exists.
  assert.equal(suggestedBatchSize(10, ["a", "b", "c"]), 10);
  assert.equal(suggestedBatchSize(400, ["a", "b", "c"]), 3 * SUGGESTED_QUESTIONS_PER_SKILL);
});

test("a batch at or above the suggestion says nothing", () => {
  const w = review({ values: { learning_rate: 8, batch_size: 40, gate_metric: "soft" } });
  assert.ok(!ids(w).includes("batch-suggestion"));
  assert.ok(!ids(w).includes("thin-batch"));
});

test("a strict set match over a small validation split is reported", () => {
  // `hard` is set equality. Over a couple of dozen questions it moves in steps
  // of 1/N and can sit at zero for the first several, which leaves the gate's
  // "strictly greater" nothing to compare and rejects every candidate.
  const split = splitOf(
    Array.from({ length: 30 }, (_, i) => q(`t${i}`, ["billing"])),
    { val: ["v1", "v2", "v3"] },
  );
  const w = routingReviewWarnings({
    mode: "routing",
    skills: ["billing", "reporting"],
    split,
    values: { learning_rate: 8, batch_size: 30, gate_metric: "hard" },
  });
  const found = w.find((x) => x.id === "hard-gate-small-val");
  assert.equal(found.tone, "warning");
  assert.match(found.body, /3/);
  assert.ok(SMALL_VAL_SPLIT > 3);
});

test("the same small split on the soft metric says nothing", () => {
  const split = splitOf(
    Array.from({ length: 30 }, (_, i) => q(`t${i}`, ["billing"])),
    { val: ["v1", "v2", "v3"] },
  );
  const w = routingReviewWarnings({
    mode: "routing",
    skills: ["billing", "reporting"],
    split,
    values: { learning_rate: 8, batch_size: 30, gate_metric: "soft" },
  });
  assert.ok(!ids(w).includes("hard-gate-small-val"));
});

test("optimising a single description is noted as the weaker lever it is", () => {
  const w = routingReviewWarnings({
    mode: "routing",
    skills: ["billing"],
    split: splitOf(Array.from({ length: 20 }, (_, i) => q(`b${i}`, ["billing"]))),
    values: { learning_rate: 8, batch_size: 20, gate_metric: "soft" },
  });
  const found = w.find((x) => x.id === "single-skill");
  assert.equal(found.tone, "info");
});

test("isolated raises none of the review warnings either", () => {
  assert.deepEqual(
    ids(routingReviewWarnings({
      mode: "isolated",
      skills: ["billing"],
      split: splitOf([q("a", ["billing"])]),
      values: { learning_rate: 1, batch_size: 1, gate_metric: "hard" },
    })),
    [],
  );
});

test("a field mid-edit is not treated as a zero", () => {
  // The wizard's number inputs can be empty or half-typed, and `values` carries
  // null for those. Reporting "your edit budget is below your skill count" about
  // a field someone is in the middle of clearing is noise.
  const w = review({ values: { learning_rate: null, batch_size: null, gate_metric: "soft" } });
  assert.ok(!ids(w).includes("edit-budget"));
  assert.ok(!ids(w).includes("thin-batch"));
  assert.ok(!ids(w).includes("batch-suggestion"));
});

test("no split yet raises nothing rather than raising everything", () => {
  assert.deepEqual(
    ids(routingReviewWarnings({
      mode: "routing", skills: ["billing", "reporting"], split: null,
      values: { learning_rate: 8, batch_size: 8, gate_metric: "soft" },
    })),
    [],
  );
});
