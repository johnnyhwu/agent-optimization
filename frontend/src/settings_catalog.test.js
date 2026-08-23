import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { HYPER_FIELDS } from "./optimize_wizard.js";

// The browser half of the contract in
// `backend/tests/test_settings_catalog.py`. That one watches the environment
// side: a new `Settings` field has to be offered on the settings page or
// excluded with a reason. This one watches the form side, which is the half it
// cannot see — a key only earns a user default once some control in here lets a
// developer override it for one run, and that control is added in a JSX file
// with nothing tying it back to the catalogue.
//
// Both directions are checked, because they are different mistakes:
//
//   1. a control exists for a key the catalogue does not offer — the settings
//      page silently stops covering something people retype every day
//   2. the catalogue offers a key no control uses — a preference that cannot
//      affect anything, which is worse than a missing one because it looks like
//      it works
//
// The catalogue reaches this file as generated JSON, because `node --test` runs
// plain ESM and cannot import Python. A backend test asserts the JSON matches
// `CATALOG`, so the two halves cannot drift apart without one of them going red.

const HERE = dirname(fileURLToPath(import.meta.url));

const CATALOG = JSON.parse(readFileSync(join(HERE, "settings_catalog.json"), "utf8"));
const KEYS = new Set(CATALOG.map((s) => s.key));

// Every file with a control whose value comes from the two /defaults endpoints.
const FORMS = [
  "components/RunConfigFields.jsx",
  "components/RunConfigDialog.jsx",
  "components/optimize/Wizard.jsx",
];

// How a field name appears in those files. Deliberately several patterns rather
// than one loose one: a regex broad enough to catch every shape would also catch
// half the local variables, and an assertion that fires on noise gets an
// exception added to it rather than being read.
const PATTERNS = [
  /\b(?:set|setNum|raw|switchOn)\("([a-z][a-z0-9_]*)"/g,
  /\b(?:form|values|secrets|errors|hyper)\.([a-z][a-z0-9_]*)/g,
  /\bfield="([a-z][a-z0-9_]*)"/g,
  /\.\.\.hyper,\s*([a-z][a-z0-9_]*):/g,
];

// Names a form uses that are deliberately not user defaults, and why. A bare
// list would record that somebody looked; the reasons record what they decided.
const NOT_A_SETTING = {
  name: "the run's own label, different every time — there is no default to have",
  judge_system_prompt:
    "grading criteria. For an eval run they belong to the eval set's owner so the whole team's pass rates stay comparable; for an optimization run they belong to that one experiment. Neither is a per-user preference, and neither comes from the environment.",
  reflect_budget_chars:
    "on the form but not in the environment (reflection.py's DEFAULT_REFLECT_BUDGET_CHARS). A user default is only offered where a deployment can already configure one.",
};

function referencedFieldNames() {
  const found = new Set();
  for (const file of FORMS) {
    const source = readFileSync(join(HERE, file), "utf8");
    for (const pattern of PATTERNS) {
      pattern.lastIndex = 0;
      let match;
      while ((match = pattern.exec(source))) found.add(match[1]);
    }
  }
  return found;
}

test("every field a form prefills is offered on the settings page or excused", () => {
  const undecided = [...referencedFieldNames()].filter(
    (name) => !KEYS.has(name) && !(name in NOT_A_SETTING)
  );
  assert.deepEqual(
    undecided.sort(),
    [],
    `These form fields are neither offered on the settings page nor listed in ` +
      `NOT_A_SETTING: ${undecided.sort().join(", ")}. If the key has an ` +
      `environment variable behind it, add it to settings_catalog.CATALOG. If it ` +
      `does not, add it to NOT_A_SETTING with the reason.`
  );
});

test("every setting the catalogue offers has a control behind it", () => {
  const referenced = referencedFieldNames();
  const orphans = [...KEYS].filter((key) => !referenced.has(key));
  assert.deepEqual(
    orphans.sort(),
    [],
    `The settings page offers these but no form reads them: ${orphans.sort().join(", ")}. ` +
      `A preference that cannot change anything is worse than a missing one — it ` +
      `looks like it works.`
  );
});

test("NOT_A_SETTING carries a reason for each name and no stale ones", () => {
  const referenced = referencedFieldNames();
  for (const [name, why] of Object.entries(NOT_A_SETTING)) {
    assert.ok(why && why.trim(), `${name} is excused with no reason`);
    assert.ok(
      referenced.has(name),
      `${name} is excused but no form mentions it any more — drop the entry`
    );
  }
});

// --- Bounds -----------------------------------------------------------------

test("the catalogue is no looser than the wizard's own limits", () => {
  // `concurrency` is the live example: the eval run schema accepts any integer
  // above zero and the wizard caps it at 32, so a stored default of 64 would be
  // rejected by a form the user never touched.
  for (const spec of CATALOG) {
    const limits = HYPER_FIELDS[spec.key];
    if (!limits) continue;
    if (limits.min !== undefined) {
      assert.ok(
        spec.minimum !== null && spec.minimum >= limits.min,
        `${spec.key}: catalogue minimum ${spec.minimum} is below the wizard's ${limits.min}`
      );
    }
    if (limits.max !== undefined) {
      assert.ok(
        spec.maximum !== null && spec.maximum <= limits.max,
        `${spec.key}: catalogue maximum ${spec.maximum} is above the wizard's ${limits.max}`
      );
    }
  }
});

test("concurrency takes the wizard's ceiling, not the eval schema's absence of one", () => {
  const spec = CATALOG.find((s) => s.key === "concurrency");
  assert.equal(spec.maximum, HYPER_FIELDS.concurrency.max);
});

test("a percent-scaled setting is marked as one", () => {
  // The form types 25 and the API stores 0.25. If the settings page decided that
  // on its own the two would differ by a factor of a hundred.
  for (const [key, limits] of Object.entries(HYPER_FIELDS)) {
    if (limits.scale !== 100) continue;
    const spec = CATALOG.find((s) => s.key === key);
    if (!spec) continue;
    assert.equal(
      spec.kind,
      "fraction",
      `${key} is scaled by 100 in the wizard but is not a "fraction" in the catalogue`
    );
  }
});
