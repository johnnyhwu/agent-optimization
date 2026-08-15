import test from "node:test";
import assert from "node:assert/strict";

import {
  MAX_LENGTH,
  normalizeRunName,
  runNameChanged,
  runNameError,
} from "./run_name.js";

test("a name is stored trimmed", () => {
  assert.equal(normalizeRunName("  Tune billing  "), "Tune billing");
  // Whitespace only is the same as none: the row falls back to its timestamp
  // rather than rendering a name-shaped blank that cannot be clicked.
  assert.equal(normalizeRunName("   "), "");
  assert.equal(normalizeRunName(null), "");
  assert.equal(normalizeRunName(undefined), "");
});

test("clearing the name is allowed — it is how a rename is undone", () => {
  assert.equal(runNameError(""), null);
  assert.equal(runNameError("   "), null);
});

test("a name longer than the server will take is refused before the request", () => {
  assert.equal(runNameError("a".repeat(MAX_LENGTH)), null);
  const tooLong = runNameError("a".repeat(MAX_LENGTH + 1));
  assert.ok(tooLong);
  // The number is in the message: "too long" without saying how long leaves the
  // developer deleting characters one at a time to find out.
  assert.match(tooLong, new RegExp(String(MAX_LENGTH)));
  // Trimmed first, so trailing spaces do not push an acceptable name over.
  assert.equal(runNameError(`${"a".repeat(MAX_LENGTH)}   `), null);
});

test("control characters are refused, because they render as nothing", () => {
  // A name of newlines is not empty, so the row would look unnamed while the
  // server held a value — and nothing on screen would explain the mismatch.
  assert.ok(runNameError("first\nsecond"));
  assert.ok(runNameError("tab\there"));
  assert.equal(runNameError("Tune billing — epoch 2"), null);
});

test("a save that would change nothing is not a save", () => {
  assert.equal(runNameChanged("Tune billing", "Tune billing"), false);
  // Trimming is applied to both sides: adding a space and pressing the tick is
  // not a rename, and treating it as one raises a toast for a no-op.
  assert.equal(runNameChanged("Tune billing", " Tune billing "), false);
  assert.equal(runNameChanged(null, ""), false);
  assert.equal(runNameChanged(null, "   "), false);
  assert.equal(runNameChanged("Tune billing", "Tune reporting"), true);
  // Clearing a name is a real change.
  assert.equal(runNameChanged("Tune billing", ""), true);
  assert.equal(runNameChanged(null, "Tune billing"), true);
});
