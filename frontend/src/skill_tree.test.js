import test from "node:test";
import assert from "node:assert/strict";

import { charLabel, skillTree } from "./skill_tree.js";

const files = [
  "billing/SKILL.md",
  "billing/reference/b.md",
  "billing/reference/a.md",
];
const chars = {
  "billing/SKILL.md": 2410,
  "billing/reference/a.md": 1205,
  "billing/reference/b.md": 340,
};

test("a skill reads as the directory it is", () => {
  const rows = skillTree("billing", files, chars);
  assert.deepEqual(
    rows.map((r) => `${"  ".repeat(r.depth)}${r.name}`),
    [
      "billing/",
      "  reference/",
      "    a.md",
      "    b.md",
      "  SKILL.md",
    ],
  );
});

test("each file carries its own length, not the directory's total", () => {
  // The distinction the old single figure could not make: whether a skill is a
  // long SKILL.md or a short one beside a large reference — which is what an
  // isolated run's edits can and cannot reach.
  const rows = skillTree("billing", files, chars);
  const skillMd = rows.find((r) => r.name === "SKILL.md");
  assert.equal(skillMd.chars, 2410);
  assert.equal(skillMd.path, "billing/SKILL.md");
  assert.equal(rows.find((r) => r.name === "reference/").chars, null);
});

test("directories come before the files beside them", () => {
  // Otherwise `a.md` sorts above `reference/` and the nesting has to be read
  // from the indentation alone.
  const rows = skillTree("s", ["s/z.md", "s/deep/one.md"], {});
  assert.deepEqual(rows.map((r) => r.name), ["s/", "deep/", "one.md", "z.md"]);
});

test("a skill stored as one file still renders as a directory", () => {
  const rows = skillTree("billing", ["billing"], { billing: 12 });
  assert.deepEqual(rows.map((r) => r.name), ["billing/", "billing"]);
  assert.equal(rows[1].chars, 12);
});

test("a skill the agent reported no files for is still a row", () => {
  assert.deepEqual(skillTree("billing", [], {}), [
    { depth: 0, name: "billing/", isDir: true, path: null, chars: null },
  ]);
});

test("an unreported length renders as nothing rather than as zero", () => {
  assert.equal(charLabel(null), "");
  assert.equal(charLabel(undefined), "");
  assert.equal(charLabel(0), "0 characters");
  assert.equal(charLabel(1), "1 character");
  assert.equal(charLabel(2410), "2,410 characters");
});
