import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, relative } from "node:path";

// A prop with a fixed vocabulary is a contract between a component and a
// stylesheet, and nothing was checking that both sides had heard of the same
// words. `Badge tone="info"` had three call sites and no `.ui-badge-info`, so
// those three rendered an untoned pill — geometry, no colour — next to
// correctly toned ones. `Banner tone="success"` is the same bug found by
// writing this test.
//
// Two directions, because they fail differently:
//
//   1. A word the component offers with no CSS behind it — the component lies
//      about what it supports.
//   2. A word a call site passes that the component does not offer — the call
//      site invents a tone and gets an unstyled element.
//
// Neither throws. Neither shows up in a build. Both are only visible if
// somebody happens to look at that one badge on that one screen.

const HERE = dirname(fileURLToPath(import.meta.url));
const css = ["styles.css", "ui.css"]
  .map((f) => readFileSync(join(HERE, f), "utf8"))
  .join("\n")
  .replace(/\/\*[\s\S]*?\*\//g, "");

// The vocabularies, as the CSS actually implements them. Kept here rather than
// imported from the components because the components are JSX and this runs in
// plain node — so these lists are the *claim*, and the first test checks the
// claim against the stylesheet.
const VOCABULARY = {
  Badge: { prop: "tone", prefix: "ui-badge-", words: ["neutral", "info", "success", "danger", "warning", "accent"] },
  Banner: { prop: "tone", prefix: "ui-banner-", words: ["info", "error", "warning", "pending", "success"] },
  Button: { prop: "variant", prefix: "ui-btn-", words: ["primary", "secondary", "ghost", "danger", "link"] },
};

// Words that have a *bare* class rule carrying colour — `.ui-badge-info { … }`
// with a background or a colour in it.
//
// Not "the word appears somewhere in a selector": deleting the real
// `.ui-badge-info` rule left `.ui-badge-info.is-outline` behind, and a looser
// check went on passing while the tone rendered with no fill again. A modifier
// that adjusts a tone is not the tone.
function styledWords(prefix) {
  const out = new Set();
  for (const m of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const body = m[2];
    if (!/(^|;)\s*(background|color)\s*:/.test(body)) continue;
    for (const simple of m[1].split(",")) {
      const hit = simple.trim().match(new RegExp(`^\\.${prefix}([a-z]+)$`));
      if (hit) out.add(hit[1]);
    }
  }
  return out;
}

test("every word a component offers has a style behind it", () => {
  const missing = [];
  for (const [component, { prefix, words, prop }] of Object.entries(VOCABULARY)) {
    const styled = styledWords(prefix);
    for (const word of words) {
      if (!styled.has(word)) {
        missing.push(`${component} ${prop}="${word}" → .${prefix}${word} sets no colour`);
      }
    }
  }
  assert.deepEqual(missing, [], `\n  ${missing.join("\n  ")}`);
});

// --- Call sites -------------------------------------------------------------

function jsxFiles(dir) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...jsxFiles(full));
    else if (entry.endsWith(".jsx")) out.push(full);
  }
  return out;
}

// One element's opening tag, brace- and quote-aware so an attribute holding a
// `>` inside an expression does not truncate it.
function openingTags(source, name) {
  const out = [];
  for (const m of source.matchAll(new RegExp(`<${name}\\b`, "g"))) {
    let i = m.index + m[0].length;
    let depth = 0;
    let quote = null;
    for (; i < source.length; i += 1) {
      const ch = source[i];
      if (quote) {
        if (ch === quote) quote = null;
      } else if (ch === '"' || ch === "'") quote = ch;
      else if (ch === "{") depth += 1;
      else if (ch === "}") depth -= 1;
      else if (ch === ">" && depth === 0) break;
    }
    out.push(source.slice(m.index, i));
  }
  return out;
}

// The literal words a tag passes for `prop`. Two forms are read: `prop="word"`,
// and string literals in the *result* position of an expression —
// `prop={x ? "a" : "b"}`, `prop={MAP[k] || "neutral"}`. A literal in a
// comparison (`x === "reject" ? …`) is not a value and is deliberately skipped.
//
// `prop={someFunction(x)}` yields nothing and is unchecked; there is no way to
// know statically. `accuracyTone` and `STATUS_TONE` are the two such cases here.
function literalsFor(tag, prop) {
  const direct = tag.match(new RegExp(`\\b${prop}="([a-z_]+)"`));
  if (direct) return [direct[1]];

  const expr = tag.match(new RegExp(`\\b${prop}=\\{`));
  if (!expr) return [];
  let i = expr.index + expr[0].length;
  let depth = 1;
  const start = i;
  for (; i < tag.length && depth > 0; i += 1) {
    if (tag[i] === "{") depth += 1;
    else if (tag[i] === "}") depth -= 1;
  }
  const body = tag.slice(start, i - 1);
  return [...body.matchAll(/(^|\?|:|\|\||\?\?)\s*"([a-z_]+)"/g)].map((m) => m[2]);
}

test("no call site passes a word its component does not offer", () => {
  const problems = [];
  for (const file of jsxFiles(join(HERE, "components"))) {
    const source = readFileSync(file, "utf8");
    for (const [component, { prop, words }] of Object.entries(VOCABULARY)) {
      for (const tag of openingTags(source, component)) {
        for (const word of literalsFor(tag, prop)) {
          if (!words.includes(word)) {
            problems.push(`${relative(HERE, file)}: <${component} ${prop}="${word}">`);
          }
        }
      }
    }
  }
  assert.deepEqual(
    problems,
    [],
    `these render with the component's base geometry and no tone at all:\n  ` +
      problems.join("\n  "),
  );
});

test("the scanner reads the shapes this codebase actually writes", () => {
  // Guarding the guard: a scanner that silently matches nothing passes every
  // test in this file. Each case below is a real shape from these components.
  const sample = `
    <Badge tone="accent">a</Badge>
    <Badge tone={owner ? "success" : "neutral"} size="sm" />
    <Badge tone={STATUS_TONE[r.status] || "neutral"} />
    <Banner tone={detail.gate_action === "reject" ? "warning" : "success"} />
    <Badge tone={accuracyTone(q)} />
    <Button variant={metric === name ? "secondary" : "ghost"} />
  `;
  const badges = openingTags(sample, "Badge");
  assert.equal(badges.length, 4);
  assert.deepEqual(literalsFor(badges[0], "tone"), ["accent"]);
  assert.deepEqual(literalsFor(badges[1], "tone"), ["success", "neutral"]);
  assert.deepEqual(literalsFor(badges[2], "tone"), ["neutral"]);
  // The comparison operand is not a value and must not be read as one.
  assert.deepEqual(literalsFor(openingTags(sample, "Banner")[0], "tone"), ["warning", "success"]);
  // An opaque call yields nothing rather than a false positive.
  assert.deepEqual(literalsFor(badges[3], "tone"), []);
  assert.deepEqual(literalsFor(openingTags(sample, "Button")[0], "variant"), ["secondary", "ghost"]);
});

// --- One way to report a failure --------------------------------------------

test("an error state goes through Banner, not a bare div", () => {
  // `Banner` exists because five hand-rolled message boxes had drifted apart,
  // and its own comment says so. It won the argument for 58 call sites and lost
  // it for 17, which kept a second, poorer error style alive in parallel:
  // `<div className="error">{error}</div>` — a red box with no icon, no title,
  // no recovery, and a different padding and margin from every Banner beside it.
  //
  // What that actually put on screen is worse than inconsistent. Because the
  // div renders whatever string it is handed, an exception reached the user
  // verbatim: "Cannot read properties of undefined (reading 'analysis')" as the
  // entire content of the Playground, and "catalog is not iterable" as the
  // whole of Settings. A Banner has a title slot for a sentence a person can
  // act on and a `BannerDetail` slot for the machine's own words.
  const problems = [];
  for (const file of [join(HERE, "App.jsx"), ...jsxFiles(join(HERE, "components"))]) {
    const source = readFileSync(file, "utf8");
    for (const m of source.matchAll(/className="error"/g)) {
      const line = source.slice(0, m.index).split("\n").length;
      problems.push(`${relative(HERE, file)}:${line}`);
    }
  }
  assert.deepEqual(
    problems,
    [],
    `these render an error through the bare .error div instead of <Banner ` +
      `tone="error">:\n  ${problems.join("\n  ")}\n\n` +
      `Give the reader a sentence they can act on as the Banner's title, and ` +
      `put the raw message in <BannerDetail>.`,
  );
});

test("the bare .error block rule is gone from the stylesheet", () => {
  // Left behind, it is an invitation: the class still works, so the next error
  // state written in a hurry uses it and the vocabulary splits again.
  // `.error-text` — an inline message under a field — is a different component
  // and stays.
  const rule = /(?:^|\})\s*\.error\s*\{/.test(css);
  assert.equal(
    rule,
    false,
    "`.error` still has a block rule in the stylesheet; delete it so the " +
      "class cannot be reached by a new call site.",
  );
});

// --- Every component a file renders, it can actually reach ------------------

// Names a file can render: everything it imports, everything it declares, and
// everything it binds by destructuring. Split out from the test so the
// self-check below can drive it with sources whose answer is known.
function outOfScope(source) {
  const inScope = new Set(["React", "Fragment"]);

  // `^\s*`, not `^`: anchoring hard to column 0 read every indented import as
  // absent, which the self-check below caught the moment it was written.
  for (const m of source.matchAll(/^\s*import\s+([\s\S]*?)\s+from\s+["'][^"']+["'];/gm)) {
    const clause = m[1];
    // `Default`, `{ A, B as C }`, `* as NS`, and the combinations of them.
    const named = clause.match(/\{([\s\S]*?)\}/);
    if (named) {
      for (const part of named[1].split(",")) {
        const name = part.trim().split(/\s+as\s+/).pop().trim();
        if (name) inScope.add(name);
      }
    }
    const head = clause.replace(/\{[\s\S]*?\}/, "").replace(/\*\s+as\s+/, "");
    for (const part of head.split(",")) {
      const name = part.trim();
      if (/^[A-Za-z_$][\w$]*$/.test(name)) inScope.add(name);
    }
  }
  // Declared in the file itself — usually a small helper beside the export.
  for (const m of source.matchAll(/(?:^|\n)\s*(?:export\s+)?(?:default\s+)?function\s+([A-Z][\w$]*)/g)) {
    inScope.add(m[1]);
  }
  for (const m of source.matchAll(/(?:^|\n)\s*(?:export\s+)?(?:const|let|var)\s+([A-Z][\w$]*)/g)) {
    inScope.add(m[1]);
  }
  // Bound by destructuring, which is how a component arrives as data here:
  // `{ icon: Icon }` off a SECTIONS row, `as: Tag = "div"` off Card's props.
  for (const m of source.matchAll(/[{,]\s*[\w$]+\s*:\s*([A-Z][\w$]*)/g)) {
    inScope.add(m[1]);
  }
  // And by shorthand — `const { Icon, tone } = MARKS[kind]`, which is how
  // ScriptRunPanel and RolloutDetail pull a component out of a lookup table.
  for (const m of source.matchAll(/[{,]\s*([A-Z][\w$]*)\s*[,}=]/g)) {
    inScope.add(m[1]);
  }

  // Only capitalised tags are components; lowercase ones are host elements.
  // `Foo.Bar` is checked on `Foo`, which is what has to be in scope.
  const out = [];
  for (const m of source.matchAll(/<([A-Z][\w$]*)/g)) {
    if (inScope.has(m[1])) continue;
    out.push({ name: m[1], line: source.slice(0, m.index).split("\n").length });
  }
  return out;
}

test("no JSX element is used without being imported or defined", () => {
  // There is no test renderer and no jsdom here, so a component referenced but
  // never imported throws only when a human happens to open that screen. It is
  // the one mistake this codebase's tooling is completely blind to, and
  // migrating seventeen error states onto `Banner` produced exactly it:
  // `DefaultsPanel` already had `import Banner from …`, so an import pass that
  // keyed on the word "Banner" skipped the file, and `<BannerDetail>` inside it
  // was an undefined identifier. The Settings page threw on load.
  const problems = [];
  for (const file of [join(HERE, "App.jsx"), ...jsxFiles(join(HERE, "components"))]) {
    for (const { name, line } of outOfScope(readFileSync(file, "utf8"))) {
      problems.push(`${relative(HERE, file)}:${line}: <${name}> is not in scope`);
    }
  }
  assert.deepEqual(
    problems,
    [],
    `these throw at render time and nothing else here would catch it:\n  ` +
      problems.join("\n  "),
  );
});

test("the scope scanner still catches a missing import", () => {
  // Guarding the guard, as this file already does for its tone scanner. The
  // binding forms above were each added to silence a false positive, and every
  // one of them widened what counts as "in scope" — so the thing that actually
  // matters is that the real bug is still detected.
  const missing = `
    import Banner from "./ui/Banner.jsx";
    export default function Panel() {
      return <Banner tone="error"><BannerDetail>x</BannerDetail></Banner>;
    }
  `;
  assert.deepEqual(outOfScope(missing).map((p) => p.name), ["BannerDetail"]);

  // …and that the fix clears it.
  const fixed = missing.replace(
    'import Banner from "./ui/Banner.jsx";',
    'import Banner, { BannerDetail } from "./ui/Banner.jsx";',
  );
  assert.deepEqual(outOfScope(fixed), []);

  // Each real binding form, still recognised.
  assert.deepEqual(outOfScope('const Tag = "div"; const x = <Tag />;'), []);
  assert.deepEqual(outOfScope('const { Icon } = M; const x = <Icon />;'), []);
  assert.deepEqual(outOfScope('m(({ icon: Icon }) => <Icon />);'), []);
  assert.deepEqual(outOfScope('function Helper() {} const x = <Helper />;'), []);
  assert.deepEqual(outOfScope('import * as NS from "x"; const a = <NS.Thing />;'), []);
  // A lowercase host element is never a component.
  assert.deepEqual(outOfScope("const x = <div><span /></div>;"), []);
});
