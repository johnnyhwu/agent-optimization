import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

// One eval set has two overflow menus — the card in the grid and the header of
// the set's own page — and they are supposed to be the same menu.
//
// They were written twice and drifted, in a way the developer meets rather than
// the reviewer: the same set offered "Download…" in the grid and
// "Download results…" one click later, and "Edit questions" existed only on the
// inner page. Both copies now render `EvalSetMenu`, and this checks they still
// do, because the failure mode of the old code was two correct-looking files.
//
// Delete is the one deliberate difference: the grid passes `onDelete`, the
// inner page does not, since deleting the set you are looking at leaves you on
// a page for a set that no longer exists.

const HERE = dirname(fileURLToPath(import.meta.url));
const CALL_SITES = ["components/EvalSetList.jsx", "components/RunHistory.jsx"];

const source = (rel) => readFileSync(join(HERE, rel), "utf8");

// Prop *names* on the single `<EvalSetMenu … />` element in a file. Names are
// enough: what drifted was which items existed, and an item exists here exactly
// when its handler is passed.
function menuProps(jsx) {
  const el = jsx.match(/<EvalSetMenu\b([\s\S]*?)\/>/);
  assert.ok(el, "no <EvalSetMenu …/> found");
  const body = el[1]
    // Drop comments and nested JSX expressions so `onDelete={owner ? … : …}`
    // contributes its name and nothing else.
    .replace(/\/\/[^\n]*/g, "")
    .replace(/\{[^{}]*\}/g, "");
  return new Set([...body.matchAll(/(?:^|\s)([a-zA-Z]+)=/g)].map((m) => m[1]));
}

test("both eval-set menus come from the one component", () => {
  for (const rel of CALL_SITES) {
    const jsx = source(rel);
    assert.match(jsx, /import EvalSetMenu from "\.\/EvalSetMenu\.jsx"/, `${rel} does not use EvalSetMenu`);
    assert.doesNotMatch(
      jsx,
      /<Menu\b/,
      `${rel} builds its own eval-set menu again — put the item in EvalSetMenu ` +
        `so both places get it.`,
    );
  }
});

test("the two menus differ only by Delete", () => {
  const [grid, page] = CALL_SITES.map((rel) => menuProps(source(rel)));

  const onlyGrid = [...grid].filter((p) => !page.has(p));
  const onlyPage = [...page].filter((p) => !grid.has(p));

  assert.deepEqual(
    { onlyGrid, onlyPage },
    { onlyGrid: ["onDelete"], onlyPage: [] },
    "the card's menu and the set page's menu have drifted apart again",
  );
  for (const p of ["onDownload", "onEditQuestions", "onConfigure"]) {
    assert.ok(grid.has(p), `the card's menu is missing ${p}`);
  }
});
