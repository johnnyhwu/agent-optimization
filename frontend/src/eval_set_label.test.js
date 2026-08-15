import test from "node:test";
import assert from "node:assert/strict";

import { evalSetLabel, shortId } from "./eval_set_label.js";

test("two sets with one name are told apart by their ids", () => {
  // The case this exists for. Nothing stops an owner naming two sets the same
  // thing, and on the skill step that is the column a developer reads to find
  // out which of them a question came from.
  const a = evalSetLabel({
    eval_set_name: "Billing questions",
    eval_set_id: "a3f19c2e-0b41-4c76-9d55-1f2e3a4b5c6d",
  });
  const b = evalSetLabel({
    eval_set_name: "Billing questions",
    eval_set_id: "7d02be51-0b41-4c76-9d55-1f2e3a4b5c6d",
  });
  assert.equal(a.name, b.name);
  assert.notEqual(a.id, b.id);
  assert.equal(a.id, "a3f19c2e");
});

test("the whole id is kept for anyone who needs to paste it", () => {
  const label = evalSetLabel({
    eval_set_name: "Set", eval_set_id: "a3f19c2e-0b41-4c76-9d55-1f2e3a4b5c6d",
  });
  assert.equal(label.fullId, "a3f19c2e-0b41-4c76-9d55-1f2e3a4b5c6d");
});

test("a missing name does not leave the column blank", () => {
  assert.equal(evalSetLabel({ eval_set_id: "x" }).name, "(unnamed set)");
  assert.equal(evalSetLabel({}).name, "(unnamed set)");
});

test("an id of another shape is cut to the same width", () => {
  assert.equal(shortId("abcdefghijklmnop"), "abcdefgh");
  assert.equal(shortId("short"), "short");
  assert.equal(shortId(null), "");
  assert.equal(shortId(undefined), "");
});
