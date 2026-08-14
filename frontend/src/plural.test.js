import test from "node:test";
import assert from "node:assert/strict";
import { plural, pluralise } from "./plural.js";

test("one is singular, everything else is not", () => {
  assert.equal(plural(1, "question"), "1 question");
  assert.equal(plural(2, "question"), "2 questions");
  assert.equal(plural(0, "question"), "0 questions");
});

test("an irregular plural is given, not guessed", () => {
  assert.equal(plural(1, "entry", "entries"), "1 entry");
  assert.equal(plural(3, "entry", "entries"), "3 entries");
});

test("the word alone, for sentences that already counted", () => {
  assert.equal(pluralise(1, "file"), "file");
  assert.equal(pluralise(4, "file"), "files");
});
