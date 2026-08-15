import test from "node:test";
import assert from "node:assert/strict";

import { runStartedAt, runTitle } from "./optimize_run_label.js";

const run = (extra = {}) => ({
  id: "r1",
  name: null,
  skill_name: "billing",
  started_at: "2026-08-14T21:31:02Z",
  ...extra,
});

test("a named run is called what it was named", () => {
  assert.equal(runTitle(run({ name: "Tighten refunds" })), "Tighten refunds");
});

test("the rail and the panel cannot disagree about an unnamed run", () => {
  // The bug this module exists for: two components, two fallbacks, one run
  // appearing under a timestamp in the list and a sentence on the page.
  assert.equal(runTitle(run()), "Optimizing billing");
});

test("a name that is only spaces is not a name", () => {
  assert.equal(runTitle(run({ name: "   " })), "Optimizing billing");
});

test("there is always something to render", () => {
  assert.equal(runTitle({}), "Optimization run");
  assert.equal(runTitle(undefined), "Optimization run");
});

test("the start time is fixed-width so a column of them lines up", () => {
  // Local time by design — the reader's, not UTC — so the assertion is on the
  // shape rather than on an hour that depends on where the test runs.
  assert.match(runStartedAt(run()), /^\d{4}\/\d{2}\/\d{2} \d{2}:\d{2}$/);
});

test("a missing or unparseable start time renders as nothing, not as Invalid Date", () => {
  assert.equal(runStartedAt({}), "");
  assert.equal(runStartedAt({ started_at: "not a date" }), "");
});
