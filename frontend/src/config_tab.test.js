import test from "node:test";
import assert from "node:assert/strict";
import { initialConfigTab } from "./config_tab.js";

test("Settings opens on General once the grading has been reviewed", () => {
  assert.equal(
    initialConfigTab({ judge_prompt: { reviewed_at: "2026-01-04T10:00:00Z" } }),
    "general",
  );
});

test("a set whose grading nobody has read opens on Judging", () => {
  assert.equal(initialConfigTab({ judge_prompt: { reviewed_at: null } }), "judging");
  // A set with no judge prompt on the payload at all has certainly not had one
  // reviewed — the absent case must not read as "reviewed".
  assert.equal(initialConfigTab({}), "judging");
  assert.equal(initialConfigTab(undefined), "judging");
});
