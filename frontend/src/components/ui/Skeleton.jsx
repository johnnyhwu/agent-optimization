import React from "react";

// One loading treatment.
//
// There were three: shimmering blocks on the eval-set list, a bare
// `<p className="muted">Loading…</p>` on the run history, and a third wording
// inside the run picker. Which one you got depended on which screen you happened
// to be on, which reads as three different apps.
//
// Shimmer over text is also the better default here: it reserves the space the
// content will occupy, so the page doesn't jump when the data lands.
export default function Skeleton({ variant = "card", count = 1, height, className = "" }) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => (
        <div
          key={i}
          className={`ui-skeleton ui-skeleton-${variant} ${className}`.trim()}
          style={height ? { height } : undefined}
          // Purely decorative: a screen reader announcing three shimmering
          // rectangles is worse than it announcing nothing.
          aria-hidden="true"
        />
      ))}
    </>
  );
}

// The grid version, so a list of loading cards matches the grid of real ones
// rather than stacking full-width above where they will appear.
export function SkeletonCards({ count = 3 }) {
  return (
    <div className="ui-skeleton-cards">
      <Skeleton variant="card" count={count} />
    </div>
  );
}
