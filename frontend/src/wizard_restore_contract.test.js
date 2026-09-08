import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

// A restored draft is still there a second after the wizard opens.
//
// `retainedSkills` is unit-tested in `optimize_wizard.test.js`; what cannot be
// tested there is that the wizard calls it, and that is where the bug was. The
// rule is a wiring order no assertion about a pure function can reach, and
// `node --test` loads no JSX — so this reads the source, as
// `session_header_contract.test.js` and `jsx_state_setters.test.js` do.
//
// The bug it exists for: restoring the draft calls `setSourceIds`, which is a
// dependency of the preview effect, so ~300ms later `loadPreview` ran and
// unconditionally did `setSkills([]); setSkillTouched(false); rebuildSplit(null)`.
// The restored skill selection vanished while the "Picked up where you left
// off" banner still promised it — and the save effect then wrote the emptied
// draft back over the good one, so it was gone for good rather than one reload
// from returning.
//
// Two things have to hold, and both are invisible in a diff: the clear is
// conditional, and the retained names are checked against the preview that just
// arrived rather than the `preview` state, which has not landed in this tick.

const HERE = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(
  join(HERE, "components", "optimize", "Wizard.jsx"),
  "utf8",
);
const code = source
  .split("\n")
  .filter((line) => !line.trim().startsWith("//"))
  .join("\n");

const loadPreview = code.slice(
  code.indexOf("async function loadPreview"),
  code.indexOf("const sourceKey"),
);

test("a preview that arrives consults the selection before clearing it", () => {
  assert.match(
    loadPreview,
    /const kept = skillTouched \? retainedSkills\(skills, result\?\.groups\) : \[\];/,
    "an unconditional clear here is what wiped a restored draft",
  );
  assert.match(loadPreview, /if \(kept\.length\) \{/);
});

test("the retained selection is judged against the preview that just arrived", () => {
  // `setPreview(result)` has not landed by the time this runs, so reading the
  // `preview` state here would rebuild the split from the sources being
  // replaced — the previous ones.
  assert.doesNotMatch(
    loadPreview,
    /retainedSkills\(skills, preview/,
    "read `result`, not the `preview` state that this tick is replacing",
  );
  assert.match(loadPreview, /chooseSkills\(kept, \{ touched: true, groups: result\?\.groups \}\)/);
});

test("chooseSkills takes the groups to read rather than only the state", () => {
  // The parameter is the whole reason the call above can be correct.
  assert.match(
    code,
    /function chooseSkills\(names, \{ touched = true, groups = preview\?\.groups \} = \{\}\)/,
  );
});

test("the restore still marks the selection as the developer's own", () => {
  // `skillTouched` is what the guard above keys on: restored skills that were
  // not marked would be treated as the wizard's own default and re-picked.
  assert.match(code, /setSkills\(draft\.skills\);\s*\n\s*setSkillTouched\(true\);/);
});
