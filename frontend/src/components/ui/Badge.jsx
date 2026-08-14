import React from "react";

// One small rounded label, replacing six.
//
// `.pill` (run status), `.chip` (judge fingerprint), `.badge` (regression counts),
// `.tag` (metadata), `.rolechip` (owner/viewer) and `.rail-tag` (Soon) were six
// class families rendering the same object at five different font sizes with five
// different paddings. Nothing on screen lined up, because nothing on screen was
// the same component.
//
// Meaning is carried by `tone`, and the tones are the ones this product actually
// has opinions about:
//
//   neutral   a fact — a metadata key, a count, a name
//   info      an alias of neutral, for call sites thinking in Banner's
//             vocabulary. Banner names this tone `info` and renders it the same
//             quiet way, and a badge should not need to know which of the two
//             components it is standing next to. Not a sixth colour: this
//             palette has one accent on purpose.
//   success   it passed / it is current
//   danger    it failed / it broke
//   warning   it needs a human's attention but nothing is broken
//   accent    it is live right now
//
// `outline` drops the fill for use on an already-tinted surface, and `mono` is for
// values that are identifiers rather than words — fingerprints, versions, ids —
// which read as noise in the UI face and as data in the mono one.
export default function Badge({
  tone = "neutral",
  outline = false,
  mono = false,
  size = "md",
  icon,
  className = "",
  children,
  ...rest
}) {
  const cls = [
    "ui-badge",
    `ui-badge-${tone}`,
    outline && "is-outline",
    mono && "is-mono",
    size !== "md" && `ui-badge-${size}`,
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <span className={cls} {...rest}>
      {icon && <span className="ui-badge-icon">{icon}</span>}
      {children}
    </span>
  );
}

// A row of them. Exists so callers stop hand-rolling `display:flex; gap:6px;
// flex-wrap:wrap` — which they did in four places, with three different gaps.
export function BadgeRow({ className = "", children }) {
  return <div className={`ui-badge-row ${className}`.trim()}>{children}</div>;
}
