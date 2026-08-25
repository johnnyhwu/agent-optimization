// The workspace helpers, which decide what an attempt claims it changed.
//
// These were untested for as long as they existed, which mattered least while
// they were four functions among a dozen config helpers and matters most now
// that they are all that is left: an override is nothing but a skill file set,
// so `editedFiles` is the entire summary line and `sameSkills` is the entire
// decision about whether to send an override at all.
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  editedFiles,
  overrideCounts,
  sameSkills,
  skillOf,
} from "./workspace_util.js";

const BASE = {
  "billing/SKILL.md": "# Billing",
  "billing/references/refunds.md": "# Refunds",
  "reporting/SKILL.md": "# Reporting",
};

test("an untouched copy reports no edits", () => {
  assert.deepEqual(editedFiles(BASE, { ...BASE }), []);
  assert.equal(sameSkills(BASE, { ...BASE }), true);
});

test("a changed file is reported", () => {
  const edited = { ...BASE, "billing/SKILL.md": "# Billing (edited)" };
  assert.deepEqual(editedFiles(BASE, edited), ["billing/SKILL.md"]);
  assert.equal(sameSkills(BASE, edited), false);
});

test("an added file is reported", () => {
  const edited = { ...BASE, "billing/references/new.md": "# New" };
  assert.deepEqual(editedFiles(BASE, edited), ["billing/references/new.md"]);
});

test("a deleted file is reported, because removing one is an edit with a result", () => {
  // `skills` replaces the agent's directory, so a file that is absent from the
  // copy is a file the agent will not have for that call. Reporting only
  // additions and changes would let the most drastic edit available go unnamed.
  const edited = { ...BASE };
  delete edited["billing/references/refunds.md"];
  assert.deepEqual(editedFiles(BASE, edited), ["billing/references/refunds.md"]);
  assert.equal(sameSkills(BASE, edited), false);
});

test("changes, additions and deletions are reported together and sorted", () => {
  const edited = {
    "billing/SKILL.md": "# Billing (edited)",
    "reporting/SKILL.md": "# Reporting",
    "billing/references/new.md": "# New",
  };
  assert.deepEqual(editedFiles(BASE, edited), [
    "billing/SKILL.md",
    "billing/references/new.md",
    "billing/references/refunds.md",
  ]);
});

test("an empty copy reports every file as deleted", () => {
  // This is the "run with no skills at all" experiment, and it has to read as
  // three deletions rather than as no change.
  assert.deepEqual(editedFiles(BASE, {}), [
    "billing/SKILL.md",
    "billing/references/refunds.md",
    "reporting/SKILL.md",
  ]);
  assert.equal(sameSkills(BASE, {}), false);
});

test("an empty agent workspace is unchanged by an empty copy", () => {
  assert.deepEqual(editedFiles({}, {}), []);
  assert.equal(sameSkills({}, {}), true);
});

test("a file whose text is unchanged is not reported even if it moved position", () => {
  const reordered = {
    "reporting/SKILL.md": "# Reporting",
    "billing/SKILL.md": "# Billing",
    "billing/references/refunds.md": "# Refunds",
  };
  assert.deepEqual(editedFiles(BASE, reordered), []);
});

test("skillOf takes the top-level directory", () => {
  assert.equal(skillOf("billing/SKILL.md"), "billing");
  assert.equal(skillOf("billing/references/refunds.md"), "billing");
});

test("a file at the root is its own group rather than an error", () => {
  assert.equal(skillOf("README.md"), "");
});

test("overrideCounts counts the files an attempt replaced", () => {
  assert.deepEqual(overrideCounts({ edited_skill_files: ["a", "b"] }), { files: 2 });
});

test("an attempt with no override counts zero rather than throwing", () => {
  assert.deepEqual(overrideCounts({}), { files: 0 });
  assert.deepEqual(overrideCounts({ edited_skill_files: null }), { files: 0 });
});
