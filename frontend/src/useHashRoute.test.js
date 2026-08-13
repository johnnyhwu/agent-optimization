import test from "node:test";
import assert from "node:assert/strict";
import { href, parseHash } from "./useHashRoute.js";

// The Optimize section's deep routes, which are the ones that can collide.
// `#/optimize/{runId}/steps/{n}/…` has one slot left at the end and two things
// want it: the rollout's split, and Part 2's skill diff.

test("the skill diff and the two rollouts share a path shape and stay distinct", () => {
  // The split falls back to `train` for anything it does not recognise, which
  // is right for a typo and catastrophic for `skill`: the link would open the
  // training rollout — a real page, with real numbers, that nobody asked for
  // and that gives no hint it is the wrong one.
  assert.deepEqual(parseHash("#/optimize/r1/steps/3/skill"), {
    section: "optimize", tier: "skill", runId: "r1", stepNo: 3,
  });
  assert.deepEqual(parseHash("#/optimize/r1/steps/3/train"), {
    section: "optimize", tier: "rollout", runId: "r1", stepNo: 3, split: "train",
  });
  assert.deepEqual(parseHash("#/optimize/r1/steps/3/val"), {
    section: "optimize", tier: "rollout", runId: "r1", stepNo: 3, split: "val",
  });
});

test("every optimize link builder round-trips through the parser", () => {
  // The builders exist so callers link by intent instead of concatenating
  // strings. That only helps if what they produce is what the parser reads —
  // a builder that drifts from the parser sends the user to the run overview
  // and looks like a page that failed to load.
  assert.deepEqual(parseHash(href.optimizeSkill("r1", 0)), {
    section: "optimize", tier: "skill", runId: "r1", stepNo: 0,
  });
  assert.deepEqual(parseHash(href.optimizeRollout("r1", 2, "val")), {
    section: "optimize", tier: "rollout", runId: "r1", stepNo: 2, split: "val",
  });
  assert.equal(parseHash(href.optimizeRun("r1")).tier, "run");
  assert.equal(parseHash(href.optimizeNew()).tier, "new");
  assert.equal(parseHash(href.optimize()).tier, "runs");
});

test("step 0 is a real address, not a falsy one", () => {
  // The baseline is step *zero*, so any `if (stepNo)` guard on the way in drops
  // it. Its diff is empty by definition, which makes a wrong route here look
  // like a page that merely had nothing to show.
  assert.deepEqual(parseHash("#/optimize/r1/steps/0/skill"), {
    section: "optimize", tier: "skill", runId: "r1", stepNo: 0,
  });
  assert.equal(parseHash("#/optimize/r1/steps/0/val").stepNo, 0);
});

test("a truncated deep link degrades to the run rather than to a broken page", () => {
  // Hashes get hand-edited and truncated by chat clients. Falling back to the
  // run overview keeps the runId that was still in the URL.
  assert.deepEqual(parseHash("#/optimize/r1/steps"), {
    section: "optimize", tier: "run", runId: "r1",
  });
  assert.equal(parseHash("#/optimize").tier, "runs");
});

test("the section is not confused with the evaluation routes it sits beside", () => {
  assert.equal(parseHash("#/playground").section, "playground");
  assert.equal(parseHash("#/evaluation").section, "evaluation");
  assert.equal(parseHash("").section, "evaluation");
});
