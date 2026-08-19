import assert from "node:assert/strict";
import test from "node:test";

import { secs } from "./duration.js";

test("nothing measured is a dash, not a zero", () => {
  // A span that reported no duration and a span that took no time are different
  // claims, and only one of them is ever true.
  assert.equal(secs(null), "—");
  assert.equal(secs(undefined), "—");
  assert.equal(secs(0), "0ms");
});

test("under a second reads in milliseconds", () => {
  // `0.2s` and `0.9s` are the same number to someone skimming a column for the
  // step that is unlike its neighbours, which is the only reason to show this.
  assert.equal(secs(180), "180ms");
  assert.equal(secs(999), "999ms");
});

test("a second and over reads in seconds, to one place", () => {
  assert.equal(secs(1000), "1.0s");
  assert.equal(secs(2449), "2.4s");
  assert.equal(secs(120_000), "120.0s");
});
