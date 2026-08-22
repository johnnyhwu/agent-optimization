// Run with: pnpm test  (node --test)
import { test } from "node:test";
import assert from "node:assert/strict";
import { skillCoverage, coverageNote } from "./skill_coverage.js";

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

// --- The sentence under the warning -----------------------------------------

test("the note names the skill and what depends on it", () => {
  const note = coverageNote(
    skillCoverage([{ skill_name: "refunds", question_count: 3 }], ["billing"]),
    0
  );
  assert.match(note, /refunds/);
  assert.match(note, /3 questions/);
});

test("a case mismatch says what the agent actually calls it", () => {
  const note = coverageNote(skillCoverage(set("Billing"), ["billing"]), 0);
  assert.match(note, /billing/);
});

test("untagged questions are reported as a count, and only when there are some", () => {
  const covered = skillCoverage(set("billing"), ["billing"]);
  assert.equal(coverageNote(covered, 0), null);
  assert.match(coverageNote(covered, 4), /4 questions have no skill tag/);
  // Singular reads as English, not as "1 questions".
  assert.match(coverageNote(covered, 1), /1 question has no skill tag/);
});
