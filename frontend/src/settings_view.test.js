import test from "node:test";
import assert from "node:assert/strict";

import { OFF, SET, SYSTEM, fromStored } from "./settings_fields.js";
import {
  barMessage,
  dirtyKeys,
  isOverridden,
  matchesQuery,
  saying,
  visibleGroups,
} from "./settings_view.js";

const CATALOG = [
  { key: "agent_chat_url", group: "agent", kind: "text", label: "Chat endpoint", help: "Where questions are sent.", optional: false },
  { key: "agent_timeout_s", group: "agent", kind: "float", label: "Agent timeout", help: "Seconds.", optional: false, minimum: 1 },
  { key: "diagnosis_enabled", group: "models", kind: "bool", label: "Diagnose wrong answers", help: "", optional: false },
  { key: "early_stop_target_score", group: "stop", kind: "fraction", label: "Good enough", help: "", optional: true },
];

const GROUPS = [
  { id: "agent", label: "Agent", description: "The service that answers." },
  { id: "models", label: "Models", description: "Grading." },
  { id: "stop", label: "Early stopping", description: "When to give up." },
];

const spec = (key) => CATALOG.find((s) => s.key === key);

test("an empty box and a box of spaces are both 'no opinion'", () => {
  // Not cosmetic: this is what stops a click into a blank field and a stray
  // space from being reported as an unsaved change and warned about on exit.
  assert.equal(saying(spec("agent_chat_url"), { mode: SYSTEM, raw: "" }), null);
  assert.equal(saying(spec("agent_chat_url"), { mode: SET, raw: "   " }), null);
  assert.equal(saying(spec("agent_chat_url"), undefined), null);
  assert.equal(saying(spec("agent_chat_url"), { mode: SET, raw: " http://a " }), "set:http://a");
});

test("a checkbox's 'off' is an opinion and the optional field's 'off' is too", () => {
  assert.equal(saying(spec("diagnosis_enabled"), { mode: SET, raw: "false" }), "set:false");
  assert.equal(saying(spec("early_stop_target_score"), { mode: OFF, raw: "" }), "off");
  assert.equal(isOverridden(spec("diagnosis_enabled"), { mode: SET, raw: "false" }), true);
  assert.equal(isOverridden(spec("diagnosis_enabled"), { mode: SYSTEM, raw: "" }), false);
});

test("dirty keys are the edits the server has not been told about", () => {
  const saved = fromStored(CATALOG, { agent_chat_url: "http://a" });
  assert.deepEqual(dirtyKeys(CATALOG, saved, saved), []);

  const typed = { ...saved, agent_timeout_s: { mode: SET, raw: "30" } };
  assert.deepEqual(dirtyKeys(CATALOG, typed, saved), ["agent_timeout_s"]);

  // Clearing an override is an edit too — the key leaves the database.
  const cleared = { ...saved, agent_chat_url: { mode: SYSTEM, raw: "" } };
  assert.deepEqual(dirtyKeys(CATALOG, cleared, saved), ["agent_chat_url"]);

  // Retyping the same value is not.
  const retyped = { ...saved, agent_chat_url: { mode: SET, raw: "http://a" } };
  assert.deepEqual(dirtyKeys(CATALOG, retyped, saved), []);
});

test("'off' and 'follow the deployment' are different unsaved states", () => {
  // The whole reason the optional field has three positions. If these compared
  // equal, switching between them would look saved already.
  const saved = fromStored(CATALOG, {});
  const off = { ...saved, early_stop_target_score: { mode: OFF, raw: "" } };
  assert.deepEqual(dirtyKeys(CATALOG, off, saved), ["early_stop_target_score"]);
});

test("search reads the label, the help, the group and the key", () => {
  assert.equal(matchesQuery(spec("agent_chat_url"), "Agent", "url"), true);
  assert.equal(matchesQuery(spec("agent_chat_url"), "Agent", "questions"), true);
  assert.equal(matchesQuery(spec("agent_chat_url"), "Agent", "agent_chat"), true);
  assert.equal(matchesQuery(spec("diagnosis_enabled"), "Models", "models"), true);
  assert.equal(matchesQuery(spec("diagnosis_enabled"), "Models", "timeout"), false);
  // Every word has to land, so a second word narrows rather than widens.
  assert.equal(matchesQuery(spec("agent_timeout_s"), "Agent", "agent timeout"), true);
  assert.equal(matchesQuery(spec("agent_chat_url"), "Agent", "agent timeout"), false);
  assert.equal(matchesQuery(spec("agent_chat_url"), "Agent", "   "), true);
});

test("a group with nothing left in it is dropped, not left as a bare heading", () => {
  const form = fromStored(CATALOG, {});
  const all = visibleGroups(CATALOG, GROUPS, form, "");
  assert.deepEqual(all.map((g) => g.id), ["agent", "models", "stop"]);

  const some = visibleGroups(CATALOG, GROUPS, form, "timeout");
  assert.deepEqual(some.map((g) => g.id), ["agent"]);
  assert.deepEqual(some[0].specs.map((s) => s.key), ["agent_timeout_s"]);

  assert.deepEqual(visibleGroups(CATALOG, GROUPS, form, "nothing here"), []);
});

test("a group's override count reports the group, not the search", () => {
  const form = fromStored(CATALOG, { agent_chat_url: "http://a", agent_timeout_s: 30 });
  const filtered = visibleGroups(CATALOG, GROUPS, form, "timeout");
  assert.equal(filtered[0].specs.length, 1);
  assert.equal(filtered[0].overridden, 2);
});

test("the action bar says the most urgent true thing", () => {
  assert.equal(barMessage({ saving: true, dirty: 3 }), "Saving…");
  assert.equal(barMessage({ dirty: 3, errors: 1 }), "Fix 1 field before saving");
  assert.equal(barMessage({ dirty: 3, errors: 2 }), "Fix 2 fields before saving");
  assert.equal(barMessage({ dirty: 1 }), "1 unsaved change");
  assert.equal(barMessage({ dirty: 2 }), "2 unsaved changes");
  assert.equal(barMessage({ dirty: 0, overridden: 1 }), "Saved · 1 setting overridden");
  assert.equal(barMessage({ dirty: 0, overridden: 4 }), "Saved · 4 settings overridden");
  assert.equal(barMessage({}), "Following this deployment on everything");
});
