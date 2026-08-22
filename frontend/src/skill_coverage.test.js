// Run with: pnpm test  (node --test)
import { test } from "node:test";
import assert from "node:assert/strict";
import { skillCoverage, coverageWarning } from "./skill_coverage.js";

const set = (...names) => names.map((skill_name) => ({ skill_name, question_count: 1 }));

test("an agent carrying every tagged skill has nothing to report", () => {
  const out = skillCoverage(set("billing", "reporting"), ["billing", "escalation", "reporting"]);
  assert.deepEqual(out.missing, []);
  assert.deepEqual(out.matched, ["billing", "reporting"]);
  assert.equal(out.ok, true);
});

test("a tag the agent does not have is named", () => {
  const out = skillCoverage(set("billing", "reporting", "refunds"), ["billing", "reporting"]);
  assert.deepEqual(out.missing.map((m) => m.skill_name), ["refunds"]);
  assert.equal(out.ok, false);
});

test("skills the agent has and nobody asks for are not a problem", () => {
  // An agent is allowed to know more than this eval set exercises. Reporting it
  // would put a warning on every correctly configured deployment, which is how
  // a warning stops being read.
  const out = skillCoverage(set("billing"), ["billing", "reporting", "escalation"]);
  assert.equal(out.ok, true);
  assert.deepEqual(out.missing, []);
});

test("a tag that differs only in case is called out as such", () => {
  // The failure nobody sees. Decision 6 matches the tag against the directory
  // name exactly, so `Billing` is as missing as `billling` is — but only one of
  // the two is fixed by looking harder at the spelling.
  const out = skillCoverage(set("Billing"), ["billing"]);
  assert.equal(out.ok, false);
  assert.deepEqual(out.missing.map((m) => m.skill_name), ["Billing"]);
  assert.equal(out.missing[0].caseMatch, "billing");
});

test("an unrelated missing tag has no case suggestion to make", () => {
  const out = skillCoverage(set("refunds"), ["billing"]);
  assert.equal(out.missing[0].caseMatch, null);
});

test("the question count rides along, so the warning can say how much is at stake", () => {
  const out = skillCoverage(
    [{ skill_name: "refunds", question_count: 12 }],
    ["billing"]
  );
  assert.equal(out.missing[0].question_count, 12);
});

test("no tags at all is not a coverage failure", () => {
  // A set nobody has tagged is a set this check knows nothing about. Calling
  // that "everything is missing" would block on the wrong thing; the untagged
  // count is what says so instead.
  const out = skillCoverage([], ["billing"]);
  assert.equal(out.ok, true);
  assert.deepEqual(out.missing, []);
});

test("an agent with no skills makes every tag missing", () => {
  const out = skillCoverage(set("billing"), []);
  assert.equal(out.ok, false);
  assert.deepEqual(out.missing.map((m) => m.skill_name), ["billing"]);
});

test("missing input is treated as nothing known, never as a failure", () => {
  assert.equal(skillCoverage(null, null).ok, true);
  assert.equal(skillCoverage(undefined, ["billing"]).ok, true);
});

// --- The warning: heading and body come from one place ----------------------
//
// They are produced together because they were once produced apart, and a
// heading that overstates its body is worse than no heading: an eval set with no
// tags at all, against a perfectly healthy agent, was told "Some questions need
// skills this agent does not have" — a claim about the agent, made on evidence
// that says nothing about the agent. A warning that cries wolf once is read as
// noise from then on.

test("the warning names the skill and what depends on it", () => {
  const w = coverageWarning(
    skillCoverage([{ skill_name: "refunds", question_count: 3 }], ["billing"]),
    0
  );
  assert.match(w.title, /does not have/);
  assert.match(w.text, /refunds/);
  assert.match(w.text, /3 questions/);
});

test("a case mismatch says what the agent actually calls it", () => {
  const w = coverageWarning(skillCoverage(set("Billing"), ["billing"]), 0);
  assert.match(w.text, /billing/);
});

test("untagged questions alone are not a claim about the agent", () => {
  const covered = skillCoverage(set("billing"), ["billing"]);
  const w = coverageWarning(covered, 4);
  assert.match(w.text, /4 questions have no skill tag/);
  // The heading has to be about what was *not checked*, not about the agent
  // lacking something — nothing here says it does.
  assert.doesNotMatch(w.title, /does not have/);
  assert.match(w.title, /not checked|could not be checked/i);
});

test("nothing missing and nothing untagged is no warning at all", () => {
  assert.equal(coverageWarning(skillCoverage(set("billing"), ["billing"]), 0), null);
});

test("a real miss keeps its heading even when there are untagged questions too", () => {
  // The more serious claim wins the heading; both sentences survive in the body.
  const w = coverageWarning(skillCoverage(set("refunds"), ["billing"]), 2);
  assert.match(w.title, /does not have/);
  assert.match(w.text, /refunds/);
  assert.match(w.text, /2 questions have no skill tag/);
});

test("singular reads as English, not as \"1 questions\"", () => {
  const w = coverageWarning(skillCoverage(set("billing"), ["billing"]), 1);
  assert.match(w.text, /1 question has no skill tag/);
});
