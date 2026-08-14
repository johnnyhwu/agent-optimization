import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

// The stylesheets' own contract, checked mechanically.
//
// Three bugs shipped because CSS fails silently and nothing was watching:
//
//   `--text-sm` was used twenty-eight times and defined nowhere. An
//   unresolvable var() is invalid at computed-value time, so rather than being
//   ignored the declaration falls back to the inherited value — every one of
//   those font sizes was wrong, on one section of the product, for as long as
//   it existed.
//
//   `.opt-section` and `.opt-groups` each meant two different things to two
//   different components, and the later rule silently governed both.
//
//   A comment at styles.css:446 records `--chrome-h` doing the same thing
//   before either of those. That is three times, which is a pattern rather
//   than an accident, and a pattern is worth thirty lines of test.
//
// These are the cheapest guards in the repo: they need no browser, no DOM and
// no rendering, and they fail on the exact class of mistake that is invisible
// in review and invisible in the running app.

const HERE = dirname(fileURLToPath(import.meta.url));
const FILES = ["styles.css", "ui.css"];

const sources = Object.fromEntries(
  FILES.map((name) => [name, readFileSync(join(HERE, name), "utf8")]),
);

// Comments are stripped before anything else, or prose describing a past bug
// reads as a live reference to it — `--chrome-h` is named in a comment for
// exactly that reason and must not be reported.
const strip = (css) => css.replace(/\/\*[\s\S]*?\*\//g, "");

const stripped = Object.fromEntries(
  Object.entries(sources).map(([name, css]) => [name, strip(css)]),
);
const allCss = Object.values(stripped).join("\n");

function definedTokens(css) {
  const out = new Set();
  for (const m of css.matchAll(/(--[\w-]+)\s*:/g)) out.add(m[1]);
  return out;
}

// `var(--x)` with no fallback. `var(--x, 1px)` is left alone: a fallback is a
// deliberate statement that the token may be absent.
function requiredTokens(css) {
  const out = [];
  for (const m of css.matchAll(/var\(\s*(--[\w-]+)\s*\)/g)) out.push(m[1]);
  return out;
}

test("every custom property used without a fallback is defined somewhere", () => {
  const defined = definedTokens(allCss);
  const missing = new Map();

  for (const [name, css] of Object.entries(stripped)) {
    for (const token of requiredTokens(css)) {
      if (defined.has(token)) continue;
      missing.set(token, (missing.get(token) || 0) + 1);
    }
  }

  assert.deepEqual(
    [...missing.entries()],
    [],
    `undefined custom properties (token → uses):\n` +
      [...missing.entries()].map(([t, n]) => `  ${t} — ${n} use(s)`).join("\n") +
      `\n\nAn unresolvable var() is invalid at computed-value time: the property ` +
      `falls back to its inherited value rather than being ignored, so nothing ` +
      `throws and the page merely renders wrong. Define it, or give the var() a fallback.`,
  );
});

test("both ways of asking for dark mode produce the same palette", () => {
  // A theme is picked two ways — the toggle writes `data-theme`, and a user who
  // has never touched it gets `prefers-color-scheme`. Both must land on the
  // same seventeen tokens, and plain CSS cannot share one declaration list
  // between a selector and a media query, so the only thing keeping them equal
  // is this test.
  //
  // The block used to carry seven of the seventeen. What a system-dark user
  // actually got: `--code-bg` still #f6f7fb under `--text` #e7ecf5, which is
  // near-white on near-white in every payload, judge comment and trace on the
  // site; a pale lavender `--bg-grad` over a near-black page; and light-mode
  // shadows, invisible on #0b0e16, so no card had any elevation.
  const styles = stripped["styles.css"];
  const declarations = (body) =>
    Object.fromEntries(
      [...body.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)].map((m) => [
        m[1],
        m[2].replace(/\s+/g, " ").trim(),
      ]),
    );

  const explicit = styles.match(/:root\[data-theme="dark"\]\s*\{([^}]*)\}/);
  const system = styles.match(
    /@media \(prefers-color-scheme: dark\)[^{]*\{\s*:root[^{]*\{([^}]*)\}/,
  );
  assert.ok(explicit, "no :root[data-theme=\"dark\"] block found");
  assert.ok(system, "no prefers-color-scheme: dark block found");

  const a = declarations(explicit[1]);
  const b = declarations(system[1]);
  const onlyExplicit = Object.keys(a).filter((k) => !(k in b));
  const onlySystem = Object.keys(b).filter((k) => !(k in a));
  const differing = Object.keys(a).filter((k) => k in b && a[k] !== b[k]);

  assert.deepEqual(
    { onlyExplicit, onlySystem, differing },
    { onlyExplicit: [], onlySystem: [], differing: [] },
    "the toggle's dark palette and the system's have drifted apart",
  );
});

test("a token is not defined only inside a media query", () => {
  // The `prefers-color-scheme` block redefines a subset of the dark tokens. A
  // token that exists *only* there is undefined for everyone whose system is
  // set the other way — which is the same silent failure with a narrower blast
  // radius.
  const outsideMedia = Object.values(stripped)
    .join("\n")
    .replace(/@media[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}/g, "");
  const definedOutside = definedTokens(outsideMedia);

  const missing = [...new Set(requiredTokens(allCss))].filter((t) => !definedOutside.has(t));
  assert.deepEqual(missing, [], `defined only inside @media: ${missing.join(", ")}`);
});

test("a font size with a token in the scale uses the token", () => {
  // 56 sizes were written as raw pixels alongside a scale that already named
  // most of them, so "make the dense text one step smaller" meant finding
  // 24 separate `12px`s. Sizes the scale does not cover — 9px and 10px in the
  // chart's SVG labels, and three one-off headings — stay literal rather than
  // growing the vocabulary by five for thirteen uses.
  const scale = new Map();
  for (const m of stripped["styles.css"].matchAll(/(--text-[\w-]+)\s*:\s*(\d+)px/g)) {
    scale.set(Number(m[2]), m[1]);
  }

  const offenders = [];
  for (const [name, css] of Object.entries(stripped)) {
    for (const m of css.matchAll(/font-size:\s*(\d+)px/g)) {
      const px = Number(m[1]);
      if (scale.has(px)) offenders.push(`${name}: font-size: ${px}px → var(${scale.get(px)})`);
    }
  }
  assert.deepEqual(offenders, [], `\n  ${offenders.join("\n  ")}`);
});

// --- Selector collisions ----------------------------------------------------

// Top-level rules only. Inside @media a repeated selector is the whole point,
// and `:root` is legitimately reopened per theme.
const EXEMPT = new Set([":root", "*", "html, body", "body"]);

// Each top-level rule as `[selector, Set(property names)]`.
function topLevelRules(css) {
  // Drop at-rule blocks wholesale, then read what is left as `selector { … }`.
  const flat = css.replace(/@[\w-]+[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}/g, "");
  const out = [];
  // Not anchored on the previous `}`: matchAll resumes after the match, which
  // has already consumed it, so an anchored pattern silently matches only the
  // first rule in the file. (Found by reintroducing a known collision and
  // watching this test pass anyway.)
  for (const m of flat.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const selector = m[1].replace(/\s+/g, " ").trim();
    if (!selector || selector.startsWith("@") || EXEMPT.has(selector)) continue;
    const props = new Set();
    for (const decl of m[2].split(";")) {
      const prop = decl.split(":")[0]?.trim();
      if (prop && !prop.startsWith("--")) props.add(prop);
    }
    out.push([selector, props]);
  }
  return out;
}

test("no selector is declared twice with the same property", () => {
  // `.opt-groups` was written twice, for two unrelated components, in one file:
  // both declared `display` and `flex-direction`, so the later rule silently
  // governed the earlier component too.
  //
  // Splitting one component's rules across a file by topic is fine and this
  // codebase does it deliberately — `.dialog-body` sets its padding where the
  // dialog is described and its flex behaviour where the fill-height case is
  // explained. What is never fine is two rules fighting over one property,
  // because only one of them is doing anything.
  const problems = [];
  for (const [name, css] of Object.entries(stripped)) {
    const bySelector = new Map();
    for (const [selector, props] of topLevelRules(css)) {
      const seen = bySelector.get(selector);
      if (!seen) {
        bySelector.set(selector, new Set(props));
        continue;
      }
      const clashes = [...props].filter((p) => seen.has(p));
      if (clashes.length) {
        problems.push(`${name}: "${selector}" redeclares ${clashes.join(", ")}`);
      }
      for (const p of props) seen.add(p);
    }
  }

  assert.deepEqual(
    problems,
    [],
    `two rules fighting over one property means only the later one is doing ` +
      `anything, and if they belong to different components one of them is ` +
      `being laid out by the other's rules:\n  ${problems.join("\n  ")}\n\n` +
      `Prefix class names by component, not by section.`,
  );
});
