import test from "node:test";
import assert from "node:assert/strict";
import { skillNote } from "./skill_tags.js";
import { parseSkillCell } from "./upload_parse.js";

test("one tag is the ordinary case and says nothing", () => {
  assert.equal(skillNote(["billing"]), null);
});

test("the two cases that drop a question out of every skill group are called out", () => {
  // Both notes have to say the consequence, not the count: "0 skills" is a fact
  // the field already shows, and what the owner cannot see is that no
  // optimization run will ever touch this question.
  for (const skills of [[], ["billing", "reports"]]) {
    const note = skillNote(skills);
    assert.ok(note, `expected a note for ${JSON.stringify(skills)}`);
    assert.match(note, /optimization run/);
  }
  assert.match(skillNote(["billing", "reports"]), /2 skills/);
});

test("a missing list reads as no tags rather than throwing", () => {
  assert.ok(skillNote(undefined));
  assert.ok(skillNote(null));
});

test("the note describes what the text box will actually send", () => {
  // The editor pairs `skillNote` with `parseSkillCell`, so a trailing comma
  // must not be read as a second, empty tag and warned about.
  assert.equal(skillNote(parseSkillCell("billing,")), null);
  assert.equal(skillNote(parseSkillCell(" billing , reports ")).slice(0, 16), "Tagged with 2 sk");
  assert.ok(skillNote(parseSkillCell("   ")));
});
