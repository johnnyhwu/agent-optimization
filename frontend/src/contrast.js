// WCAG contrast maths, as a pure module.
//
// It lives here rather than inside `css_contract.test.js` for the reason
// CLAUDE.md gives for every other rule in this codebase: a rule inside a test
// file is a rule with no test of its own, and this one is easy to get subtly
// wrong. The sRGB transfer function in particular is not `x ** 2.2` — the
// linear segment below 0.03928 is part of the definition, and dropping it
// shifts every dark colour's ratio by enough to pass a pair that fails.
//
// Alpha matters here rather than being an edge case: half this palette's badge
// backgrounds are `rgba(…, 0.14)` tints, and a ratio computed against the tint's
// *solid* colour is not merely imprecise, it is measuring a colour that never
// appears on screen. So a translucent colour has to be flattened over the
// surface behind it before it can be compared with anything.

/**
 * `#abc`, `#aabbcc`, `rgb(1, 2, 3)` or `rgba(1, 2, 3, .5)` →
 * `{ rgb: [r, g, b], alpha }`, or null for anything that is not a literal
 * colour — `var(--x)`, `transparent`, `none`, a gradient, `color-mix(…)`.
 *
 * Null rather than a fallback triple on purpose: a caller handed `[0, 0, 0]`
 * for `var(--accent)` would compute a confident, meaningless ratio against
 * black. Null is what lets the contract test skip the pair instead.
 */
export function parseColor(value) {
  const text = String(value).trim();

  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(text);
  if (hex) {
    const digits = hex[1];
    const rgb =
      digits.length === 3
        ? [...digits].map((d) => parseInt(d + d, 16))
        : [0, 2, 4].map((i) => parseInt(digits.slice(i, i + 2), 16));
    return { rgb, alpha: 1 };
  }

  const rgb = /^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)(?:[\s,/]+([\d.]+))?\s*\)$/i.exec(
    text,
  );
  if (rgb) {
    return {
      rgb: [Number(rgb[1]), Number(rgb[2]), Number(rgb[3])],
      alpha: rgb[4] === undefined ? 1 : Number(rgb[4]),
    };
  }

  return null;
}

/**
 * Flatten a possibly-translucent colour over an opaque backdrop, giving the
 * colour a viewer actually sees. Simple source-over compositing, which is what
 * a browser does for a solid backdrop.
 */
export function flatten(color, backdrop) {
  const top = typeof color === "string" ? parseColor(color) : color;
  const under = typeof backdrop === "string" ? parseColor(backdrop) : backdrop;
  if (!top || !under) return null;
  if (under.alpha !== 1) return null; // a backdrop has to be opaque to be one
  if (top.alpha === 1) return top.rgb;
  return top.rgb.map((c, i) => Math.round(c * top.alpha + under.rgb[i] * (1 - top.alpha)));
}

/** WCAG relative luminance of an `[r, g, b]` triple in 0–255. */
export function relativeLuminance([r, g, b]) {
  const linear = [r, g, b].map((channel) => {
    const c = channel / 255;
    // The low end is linear, not a power curve. Getting this wrong is the
    // classic way a contrast checker disagrees with every other one.
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
}

/**
 * Contrast ratio between two opaque colours, 1 (identical) to 21 (black on
 * white). Accepts CSS strings or `[r, g, b]` triples; anything translucent must
 * be put through `flatten` first, and passing one here returns null rather than
 * quietly measuring a colour that is not on the screen.
 *
 * Order-independent by construction: the lighter of the two is always the
 * numerator, so callers never have to know which argument is the background.
 */
export function contrastRatio(a, b) {
  const opaque = (v) => {
    if (Array.isArray(v)) return v;
    const parsed = parseColor(v);
    if (!parsed || parsed.alpha !== 1) return null;
    return parsed.rgb;
  };
  const ca = opaque(a);
  const cb = opaque(b);
  if (!ca || !cb) return null;
  const la = relativeLuminance(ca);
  const lb = relativeLuminance(cb);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

/** WCAG 2 AA minimums for a foreground/background pair. */
export const AA_NORMAL = 4.5;
export const AA_LARGE = 3;
