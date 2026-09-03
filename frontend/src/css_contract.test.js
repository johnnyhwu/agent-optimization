import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { AA_NORMAL, contrastRatio, flatten, parseColor } from "./contrast.js";

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

test("the page box grows with its content and only the fill views shrink", () => {
  // `.page` is the flex item that every screen renders into, inside `.main`,
  // which is the app's scroll container. It was `flex: 1` — grow *and* shrink —
  // so on any page longer than the window the item was squeezed to the window's
  // height while its content overflowed it. Nothing threw and the page still
  // scrolled; what silently stopped existing was everything measured from the
  // item's own box:
  //
  //   its `padding-bottom` landed mid-page, outside the scroll region, so the
  //   last control on every long screen ended flush against the bottom edge of
  //   the window with 0px under it;
  //
  //   a direct child that is its own scroll container has an automatic minimum
  //   size of zero and therefore absorbed the whole shrink — measured in
  //   headless Chromium, a child asking for 300px rendered at 2px.
  //
  // The three-column views are the exception and must keep shrinking: their
  // columns scroll internally, so the page has to end at the bottom of the
  // window rather than grow past it. That is why the shrink is scoped to
  // `:has(> .page-fill)` instead of living on `.page`.
  const styles = stripped["styles.css"];
  const flexOf = (selector) => {
    const rule = styles.match(
      new RegExp(`(?:^|})\\s*${selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*\\{([^}]*)\\}`),
    );
    assert.ok(rule, `no \`${selector}\` rule found`);
    const decl = [...rule[1].matchAll(/(?:^|;)\s*flex\s*:\s*([^;]+)/g)].pop();
    assert.ok(decl, `\`${selector}\` declares no flex`);
    return decl[1].replace(/\s+/g, " ").trim();
  };

  const shrinkOf = (flex) => {
    const parts = flex.split(" ");
    // One-value `flex: 1` is `1 1 0%`: the shrink factor is 1 by omission,
    // which is exactly how this bug was written.
    return parts.length > 1 ? Number(parts[1]) : 1;
  };

  assert.equal(
    shrinkOf(flexOf(".page")),
    0,
    "`.page` must not shrink below its content — a shrunk page box puts its " +
      "own padding-bottom outside the scroll region and squashes any child " +
      "that is a scroll container.",
  );
  // The wizard is the second of these, for the same reason: its footer is
  // pinned to the bottom of the window and its body scrolls between the step
  // bar and that footer. Before, the wizard was ordinary flow and the footer
  // sat at the end of whatever the step rendered — three steps apart in y, and
  // below the fold on the longest one, along with the sentence saying why
  // Continue was disabled.
  assert.notEqual(
    shrinkOf(flexOf(".page:has(> .opt-wizard)")),
    0,
    "the wizard must shrink to the window, or its footer cannot be pinned to " +
      "the bottom and its body has no definite height to scroll within.",
  );
  assert.notEqual(
    shrinkOf(flexOf(".page:has(> .page-fill)")),
    0,
    "the fill views must still shrink to the window, or their internally " +
      "scrolling columns grow to the length of their lists.",
  );
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

// The same walk as `topLevelRules`, but keeping the values — the colour,
// motion and target tests all need to read what a property was set *to*, not
// merely that it was set. Last declaration wins within a rule, which is what
// the cascade does.
function topLevelDeclarations(css) {
  const flat = css.replace(/@[\w-]+[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}/g, "");
  const out = [];
  for (const m of flat.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const selector = m[1].replace(/\s+/g, " ").trim();
    if (!selector || selector.startsWith("@")) continue;
    const decls = new Map();
    // Split on `;` only outside parentheses, or `rgba(1, 2, 3, .5)` survives
    // but a `transition: a 1s, b 2s` value is torn in half at the comma-free
    // semicolons it does not have. (Values here never contain a `;`.)
    for (const decl of m[2].split(";")) {
      const at = decl.indexOf(":");
      if (at < 0) continue;
      const prop = decl.slice(0, at).trim();
      if (!prop || prop.startsWith("--")) continue;
      decls.set(prop, decl.slice(at + 1).trim());
    }
    out.push([selector, decls]);
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

// --- Colour ------------------------------------------------------------------
//
// The palette failed WCAG AA almost everywhere and nothing said so, because a
// contrast failure is the quietest bug there is: the pixels are the colour they
// were asked to be. Measured before these tests existed, on white:
//
//   --green  3.30    every "owner", "improved", "accepted", "best step" badge
//   --amber  3.19    every "N ungraded" warning
//   --accent 4.47    the active rail label — and white-on-accent, which is the
//                    one primary button on every screen
//
// and in dark mode --red and --accent landed at 3.74 and 3.93 on `--panel`.
// The cause is structural rather than a series of bad picks: `--green`, `--red`
// and `--amber` are defined once, outside both theme blocks, so a single hue is
// asked to be legible on white *and* on #0b0e16. No value can do that.
//
// So the hues keep serving fills, borders, chart strokes and dots, and a
// parallel `--*-text` tier carries type, defined per theme.

// The two blocks that select a theme, plus the base every theme inherits. Named
// by regex the same way the dark-palette test above names them, rather than by
// walking the cascade, because these three are the whole cascade for tokens.
function tokensFor(theme) {
  const declarations = (body) =>
    Object.fromEntries(
      [...body.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)].map((m) => [
        m[1],
        m[2].replace(/\s+/g, " ").trim(),
      ]),
    );

  const grab = (css, pattern) => {
    const hit = css.match(pattern);
    return hit ? declarations(hit[1]) : {};
  };

  const out = {
    // ui.css opens its own `:root` for the control tokens.
    ...grab(stripped["ui.css"], /(?:^|\})\s*:root\s*\{([^}]*)\}/),
    ...grab(stripped["styles.css"], /(?:^|\})\s*:root\s*\{([^}]*)\}/),
    ...grab(stripped["styles.css"], /:root,\s*:root\[data-theme="light"\]\s*\{([^}]*)\}/),
  };
  if (theme === "dark") {
    Object.assign(out, grab(stripped["styles.css"], /:root\[data-theme="dark"\]\s*\{([^}]*)\}/));
  }
  return out;
}

// `var(--a)` → `var(--b)` → `#123456`. Bounded so a token that refers to itself
// fails the lookup rather than hanging the suite.
function resolve(value, tokens, depth = 0) {
  if (value === undefined || depth > 10) return null;
  const text = String(value).trim();
  const wrapped = /^var\(\s*(--[\w-]+)\s*\)$/.exec(text);
  if (wrapped) return resolve(tokens[wrapped[1]], tokens, depth + 1);
  return parseColor(text) ? text : null;
}

const THEMES = ["light", "dark"];

// Surfaces a piece of text can land on. `--panel-3` is in the list because
// `.ui-segmented-count` and `.skill-group` put muted text directly on it.
const SURFACES = ["--panel", "--panel-2", "--panel-3", "--bg"];

// The claim, in the style of ui_vocabulary.test.js: these are the tokens the
// design system offers for *type*, and the test below checks the claim against
// the values. `--text` and `--muted` are in the list because they are type too
// and one of them was 4.49 on `--panel-3` — under the line by a hundredth, which
// is exactly the kind of thing only a machine notices.
const TEXT_TOKENS = ["--text", "--muted", "--accent-text", "--green-text", "--red-text", "--amber-text"];

// Tinted backgrounds a badge or banner puts that text on. Translucent, so they
// are flattened over each surface before anything is measured — the ratio of a
// 14% tint against its own solid colour describes a colour that is never drawn.
const SOFT_FILLS = ["--accent-soft", "--green-soft", "--red-soft", "--amber-soft"];

test("every text token the design system offers is defined in both themes", () => {
  const missing = [];
  for (const theme of THEMES) {
    const tokens = tokensFor(theme);
    for (const name of [...TEXT_TOKENS, ...SURFACES, ...SOFT_FILLS]) {
      if (!resolve(`var(${name})`, tokens)) missing.push(`${theme}: ${name}`);
    }
  }
  assert.deepEqual(
    missing,
    [],
    `these tokens are named by the design system but do not resolve to a colour:\n  ` +
      missing.join("\n  "),
  );
});

test("every text token clears AA on every surface of its theme", () => {
  const failures = [];
  for (const theme of THEMES) {
    const tokens = tokensFor(theme);
    for (const fg of TEXT_TOKENS) {
      const colour = resolve(`var(${fg})`, tokens);
      if (!colour) continue;
      for (const surface of SURFACES) {
        const bg = resolve(`var(${surface})`, tokens);
        if (!bg) continue;
        const ratio = contrastRatio(colour, bg);
        if (ratio !== null && ratio < AA_NORMAL) {
          failures.push(`${theme}: ${fg} on ${surface} — ${ratio.toFixed(2)}:1`);
        }
      }
    }
  }
  assert.deepEqual(
    failures,
    [],
    `text below WCAG AA (4.5:1):\n  ${failures.join("\n  ")}\n\n` +
      `A hue defined once, outside both theme blocks, cannot be legible on ` +
      `white and on #0b0e16. Give it a per-theme --*-text value and leave the ` +
      `bare hue for fills, borders and chart strokes.`,
  );
});

test("a badge's text clears AA on its own tint, over any surface", () => {
  // A soft badge is three colours deep: text, on a 14–20% tint, on whatever
  // panel the badge happens to sit in. The Optimize page puts the same badge on
  // `--panel`, on `--panel-2` and straight on the page background, so all three
  // have to hold.
  const PAIRS = [
    ["--accent-text", "--accent-soft"],
    ["--green-text", "--green-soft"],
    ["--red-text", "--red-soft"],
    ["--amber-text", "--amber-soft"],
  ];
  const failures = [];
  for (const theme of THEMES) {
    const tokens = tokensFor(theme);
    for (const [fg, tint] of PAIRS) {
      const colour = resolve(`var(${fg})`, tokens);
      const fill = resolve(`var(${tint})`, tokens);
      if (!colour || !fill) continue;
      for (const surface of SURFACES) {
        const behind = resolve(`var(${surface})`, tokens);
        if (!behind) continue;
        const drawn = flatten(fill, behind);
        const ratio = drawn && contrastRatio(colour, drawn);
        if (ratio !== null && ratio < AA_NORMAL) {
          failures.push(
            `${theme}: ${fg} on ${tint} over ${surface} — ${ratio.toFixed(2)}:1`,
          );
        }
      }
    }
  }
  assert.deepEqual(failures, [], `badge text below AA:\n  ${failures.join("\n  ")}`);
});

test("every rule that sets both a colour and a background clears AA", () => {
  // The generic sweep, and the one that found `.answers .verdict.correct` —
  // white on `--green`, 3.0:1, on the verdict pill beside every answer. A
  // declared list of pairs would never have caught it, because nobody
  // remembers to add the pair they just wrote.
  const failures = [];
  for (const theme of THEMES) {
    const tokens = tokensFor(theme);
    for (const [file, css] of Object.entries(stripped)) {
      for (const [selector, decls] of topLevelDeclarations(css)) {
        if (selector.startsWith(":root")) continue;
        const fg = resolve(decls.get("color"), tokens);
        const bgRaw = decls.get("background-color") ?? decls.get("background");
        const bg = resolve(bgRaw, tokens);
        if (!fg || !bg) continue;
        // A translucent fill is flattened over every surface it could sit on;
        // an opaque one is measured as it is.
        const parsedBg = parseColor(bg);
        const backdrops =
          parsedBg.alpha === 1
            ? [bg]
            : SURFACES.map((s) => resolve(`var(${s})`, tokens))
                .filter(Boolean)
                .map((s) => flatten(bg, s))
                .filter(Boolean);
        for (const drawn of backdrops) {
          const ratio = contrastRatio(fg, drawn);
          if (ratio !== null && ratio < AA_NORMAL) {
            failures.push(`${theme}: ${file} "${selector}" — ${ratio.toFixed(2)}:1`);
            break;
          }
        }
      }
    }
  }
  assert.deepEqual(
    failures,
    [],
    `a rule paints text on a background it cannot be read on:\n  ${failures.join("\n  ")}`,
  );
});

// --- The reset ---------------------------------------------------------------

test("the bare button reset restores the inherited colour", () => {
  // `button { font: inherit }` fixed the typography and left the colour behind,
  // so any button that clears its own background without naming a colour gets
  // the UA's `buttontext` — black, in both themes. `.opt-runitem` did exactly
  // that, and the Optimize page's entire run list rendered at 1.09:1 on
  // #0b0e16 in dark mode: three rows of invisible navigation.
  //
  // Asserted on the reset rather than on `.opt-runitem`, because the next bare
  // button to clear its background would have inherited the same bug.
  const rule = stripped["styles.css"].match(/(?:^|\})\s*button\s*\{([^}]*)\}/);
  assert.ok(rule, "no bare `button` reset found in styles.css");
  const colour = [...rule[1].matchAll(/(?:^|;)\s*color\s*:\s*([^;]+)/g)].pop();
  assert.ok(
    colour && colour[1].trim() === "inherit",
    "`button` must declare `color: inherit`. Without it a button that sets no " +
      "colour of its own renders in the UA's `buttontext` — black on both " +
      "themes, which is invisible on a dark surface.",
  );
});

// --- Grid geometry -----------------------------------------------------------

test("a table cell fills its track instead of sizing to its content", () => {
  // `justify-self` on a grid item makes it size to `max-content` rather than
  // stretching to the track. `.ui-td` then clips with `overflow: hidden` at the
  // edge of a box that is *already wider than the column*, so the content spills
  // into the next one. At 768px — inside the 768–1024px band ui.css:547 says it
  // supports — the run table's header rendered as "RUŅTATUS" and every status
  // pill painted over the run name beside it.
  //
  // `text-align` does the same job without resizing the item, and reaches the
  // inline-flex Badge children too.
  const offenders = [];
  for (const [file, css] of Object.entries(stripped)) {
    for (const [selector, decls] of topLevelDeclarations(css)) {
      if (!/\.ui-al-/.test(selector)) continue;
      if (decls.has("justify-self")) offenders.push(`${file}: "${selector}"`);
    }
  }
  assert.deepEqual(
    offenders,
    [],
    `alignment classes must not set justify-self — it sizes the cell to its ` +
      `content and lets it overflow the column:\n  ${offenders.join("\n  ")}`,
  );
});

// --- Motion, stacking, elevation ---------------------------------------------

test("every transition and animation duration comes from a token", () => {
  // Ten different durations were in use — 0.12s, 0.14s, 0.15s, 0.16s, 0.18s,
  // 0.2s, 0.25s, 0.3s, 0.4s and a bare .12s — with no scale behind them, so the
  // same class of interaction moved at a different speed depending on which
  // screen it was on. That reads as cheap in a way nobody can point at.
  //
  // Two things are deliberately outside the scale and stay literal:
  //
  //   the `prefers-reduced-motion` override, whose 0.001ms is not a speed but a
  //   way of saying "effectively none" while still firing transitionend;
  //
  //   ambient loops — the shimmer, the pulse, the spinner. They are marked
  //   `infinite`, they are texture rather than a response to an action, and
  //   pinning them to an interaction speed would be meaningless.
  const offenders = [];
  for (const [file, css] of Object.entries(stripped)) {
    const interactive = css.replace(
      /@media \(prefers-reduced-motion: reduce\)\s*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}/g,
      "",
    );
    for (const m of interactive.matchAll(
      /(?:^|;|\{)\s*(transition|animation|transition-duration|animation-duration)\s*:\s*([^;}]+)/g,
    )) {
      const value = m[2].trim();
      if (/\binfinite\b/.test(value)) continue;
      for (const time of value.matchAll(/(?<![\w-])(\d*\.?\d+)(m?s)(?![\w-])/g)) {
        if (Number(time[1]) === 0) continue; // `0s` is "no duration", not a speed
        offenders.push(`${file}: ${m[1]}: ${value.replace(/\s+/g, " ")}`);
        break;
      }
    }
  }
  assert.deepEqual(
    [...new Set(offenders)],
    [],
    `literal durations, with no scale behind them:\n  ${[...new Set(offenders)].join("\n  ")}` +
      `\n\nUse the --dur-* scale so one kind of interaction moves at one speed.`,
  );
});

test("every z-index comes from a token", () => {
  // Eleven bare integers — 1, 2, 5, 20, 25, 30, 40, 50, 60, 100 — spread over
  // two files, with nothing recording which layer is meant to be above which.
  // The next overlay added to this app would have been a guess.
  const offenders = [];
  for (const [file, css] of Object.entries(stripped)) {
    for (const m of css.matchAll(/(?:^|;|\{)\s*z-index\s*:\s*([^;}]+)/g)) {
      const value = m[1].trim();
      if (value.startsWith("var(") || value === "auto") continue;
      offenders.push(`${file}: z-index: ${value}`);
    }
  }
  assert.deepEqual(
    [...new Set(offenders)],
    [],
    `bare stacking values:\n  ${[...new Set(offenders)].join("\n  ")}\n\n` +
      `Use the --z-* scale, which records the intended order in one place.`,
  );
});

test("no shadow is written as a literal colour", () => {
  // The same bug as `--shadow-1`, which the file's own comment records: a
  // light-mode navy at low alpha is simply not visible on #0b0e16, so every
  // element carrying it loses its elevation in dark mode and nothing reports
  // it. Two button rules had `0 1px 2px rgba(16, 24, 40, 0.12)` inline.
  const offenders = [];
  for (const [file, css] of Object.entries(stripped)) {
    for (const m of css.matchAll(/(?:^|;|\{)\s*box-shadow\s*:\s*([^;}]+)/g)) {
      const value = m[1].trim();
      if (/#[0-9a-f]{3,8}\b|\brgba?\(/i.test(value)) {
        offenders.push(`${file}: box-shadow: ${value.replace(/\s+/g, " ")}`);
      }
    }
  }
  assert.deepEqual(
    [...new Set(offenders)],
    [],
    `shadows with a hard-coded colour do not follow the theme:\n  ` +
      `${[...new Set(offenders)].join("\n  ")}\n\nUse --shadow-1/-2/-pop.`,
  );
});

// --- Targets and type --------------------------------------------------------

// WCAG 2.2 §2.5.8 (AA) puts the floor for a pointer target at 24×24 CSS px.
const TARGET_FLOOR = 24;

// Controls whose height comes from a `height`/`min-height` they set themselves.
// A control sized only by padding and line-height cannot be measured from the
// source, which is why the second test below names the three that were short.
test("no interactive control is sized below the 24px target floor", () => {
  const offenders = [];
  for (const [file, css] of Object.entries(stripped)) {
    for (const [selector, decls] of topLevelDeclarations(css)) {
      const interactive =
        /\bbutton\b/.test(selector) || decls.get("cursor")?.trim() === "pointer";
      if (!interactive) continue;
      for (const prop of ["height", "min-height"]) {
        const raw = decls.get(prop);
        const px = raw && /^(\d+)px$/.exec(raw.trim());
        if (px && Number(px[1]) < TARGET_FLOOR) {
          offenders.push(`${file}: "${selector}" ${prop}: ${px[1]}px`);
        }
      }
    }
  }
  assert.deepEqual(
    offenders,
    [],
    `pointer targets under ${TARGET_FLOOR}px (WCAG 2.2 AA §2.5.8):\n  ` +
      `${offenders.join("\n  ")}\n\nUse var(--ctl-h-xs) as the floor.`,
  );
});

test("the controls that were sized only by padding now declare the floor", () => {
  // These three were 22px, 22px and 21px tall, measured in the browser. Their
  // height came from line-height and padding, so no source-level rule could see
  // them; naming them is what turns "we fixed it once" into "it stays fixed".
  const REQUIRED = [
    ".ui-segmented.is-sm button",
    ".opt-chart-legend-item",
    ".breadcrumb a, .breadcrumb .current",
    ".settings-search-clear",
  ];
  const declared = new Map();
  for (const css of Object.values(stripped)) {
    for (const [selector, decls] of topLevelDeclarations(css)) {
      const size = decls.get("min-height") ?? decls.get("height");
      if (size) declared.set(selector, size.trim());
    }
  }
  const missing = REQUIRED.filter((sel) => {
    const size = declared.get(sel);
    if (!size) return true;
    if (size.includes("var(--ctl-h")) return false;
    const px = /^(\d+)px$/.exec(size);
    return !px || Number(px[1]) < TARGET_FLOOR;
  });
  assert.deepEqual(
    missing,
    [],
    `these carry meaning and are clicked, so they need an explicit height at ` +
      `or above the ${TARGET_FLOOR}px floor:\n  ${missing.join("\n  ")}`,
  );
});

test("no text is smaller than the bottom of the type scale", () => {
  // Verdicts, roles, phases and every status badge were set at 10px — the
  // smallest type in the app carrying the most load-bearing information in it,
  // in colours that were also failing AA. The scale bottoms out at
  // `--text-micro`; the chart's SVG labels are the documented exception, and
  // stay literal because they are axis furniture rather than content.
  const floor = Number(
    /--text-micro\s*:\s*(\d+)px/.exec(stripped["styles.css"])?.[1] ?? 11,
  );
  const offenders = [];
  for (const [file, css] of Object.entries(stripped)) {
    for (const [selector, decls] of topLevelDeclarations(css)) {
      if (/opt-chart/.test(selector)) continue; // axis, ticks, epoch labels
      const px = /^(\d+)px$/.exec((decls.get("font-size") ?? "").trim());
      if (px && Number(px[1]) < floor) {
        offenders.push(`${file}: "${selector}" font-size: ${px[1]}px`);
      }
    }
  }
  assert.deepEqual(
    offenders,
    [],
    `text below the ${floor}px floor of the scale:\n  ${offenders.join("\n  ")}`,
  );
});
