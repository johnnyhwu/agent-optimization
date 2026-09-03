import test from "node:test";
import assert from "node:assert/strict";
import {
  AA_NORMAL,
  contrastRatio,
  flatten,
  parseColor,
  relativeLuminance,
} from "./contrast.js";

// The reference values here are the ones the WCAG definition produces, not ones
// read off this implementation — otherwise the test only asserts that the code
// still does whatever it did, which is no guarantee at all.

const near = (actual, expected, label) =>
  assert.ok(
    actual !== null && Math.abs(actual - expected) < 0.01,
    `${label}: expected ~${expected}, got ${actual === null ? "null" : actual.toFixed(4)}`,
  );

test("parseColor reads every notation the stylesheet uses", () => {
  assert.deepEqual(parseColor("#fff"), { rgb: [255, 255, 255], alpha: 1 });
  assert.deepEqual(parseColor("#6366f1"), { rgb: [99, 102, 241], alpha: 1 });
  assert.deepEqual(parseColor("  #6366F1 "), { rgb: [99, 102, 241], alpha: 1 });
  assert.deepEqual(parseColor("rgb(99, 102, 241)"), { rgb: [99, 102, 241], alpha: 1 });
  assert.deepEqual(parseColor("rgba(99, 102, 241, 0.12)"), {
    rgb: [99, 102, 241],
    alpha: 0.12,
  });
});

test("parseColor returns null rather than a wrong triple", () => {
  for (const value of [
    "var(--accent)",
    "transparent",
    "none",
    "",
    "color-mix(in srgb, var(--panel) 80%, transparent)",
    "radial-gradient(1200px 600px at 100% -10%, #eef0ff 0%, transparent 60%)",
  ]) {
    assert.equal(parseColor(value), null, `${value} is not a literal colour`);
  }
});

test("relativeLuminance hits the endpoints of the scale", () => {
  assert.equal(relativeLuminance([0, 0, 0]), 0);
  assert.equal(relativeLuminance([255, 255, 255]), 1);
});

test("relativeLuminance uses the linear segment below the threshold", () => {
  // 10/255 = 0.0392, just under 0.03928, so it must divide by 12.92 rather than
  // take the power curve. The two differ by ~40% here, which is the difference
  // between a dark-theme pair passing and failing.
  const linear = relativeLuminance([10, 10, 10]);
  assert.ok(
    Math.abs(linear - 10 / 255 / 12.92) < 1e-12,
    "below 0.03928 the transfer function is linear",
  );
  assert.ok(
    linear > ((10 / 255 + 0.055) / 1.055) ** 2.4,
    "the linear segment is the brighter of the two",
  );
});

test("flatten composites a tint over the surface behind it", () => {
  // A 50% black over white is mid grey — the arithmetic, not the solid colour.
  assert.deepEqual(flatten("rgba(0, 0, 0, 0.5)", "#ffffff"), [128, 128, 128]);
  // An opaque colour is unchanged by whatever is behind it.
  assert.deepEqual(flatten("#16a34a", "#ffffff"), [22, 163, 74]);
});

test("flatten is what makes a soft badge measurable at all", () => {
  // `--green-soft` is 14% green. Measured as if it were solid green it looks
  // like a dark fill; flattened over white it is the pale tint actually drawn,
  // and text on it has to clear 4.5 against *that*.
  const solid = contrastRatio("#16a34a", "#ffffff");
  const tint = contrastRatio(flatten("rgba(22, 163, 74, 0.14)", "#ffffff"), "#ffffff");
  assert.ok(tint < 1.2, "the tint is nearly white");
  assert.ok(solid > 3, "the solid colour is not");
});

test("flatten refuses a backdrop that is itself translucent", () => {
  assert.equal(flatten("rgba(0, 0, 0, 0.5)", "rgba(255, 255, 255, 0.5)"), null);
});

test("contrastRatio matches the published reference pairs", () => {
  near(contrastRatio("#000000", "#ffffff"), 21, "black on white is the maximum");
  near(contrastRatio("#ffffff", "#ffffff"), 1, "a colour against itself is the minimum");
  // #767676 is the canonical "smallest grey that passes AA on white".
  near(contrastRatio("#767676", "#ffffff"), 4.54, "the AA boundary grey");
  near(contrastRatio("#777777", "#ffffff"), 4.48, "one step lighter fails");
});

test("contrastRatio does not depend on argument order", () => {
  assert.equal(contrastRatio("#16a34a", "#ffffff"), contrastRatio("#ffffff", "#16a34a"));
});

test("contrastRatio accepts triples as well as strings", () => {
  assert.equal(contrastRatio([0, 0, 0], "#ffffff"), contrastRatio("#000000", "#ffffff"));
});

test("contrastRatio returns null rather than measuring an unresolved colour", () => {
  assert.equal(contrastRatio("var(--green)", "#ffffff"), null);
  assert.equal(contrastRatio("#ffffff", "inherit"), null);
  // Translucent input is the dangerous one: it would silently report the ratio
  // of a colour that is never painted. flatten() is the way to ask this.
  assert.equal(contrastRatio("rgba(0, 0, 0, 0.5)", "#ffffff"), null);
});

test("AA_NORMAL is the 4.5 threshold the contract test asserts against", () => {
  assert.equal(AA_NORMAL, 4.5);
  assert.ok(contrastRatio("#767676", "#ffffff") >= AA_NORMAL);
  assert.ok(contrastRatio("#777777", "#ffffff") < AA_NORMAL);
});
