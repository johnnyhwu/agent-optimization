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
