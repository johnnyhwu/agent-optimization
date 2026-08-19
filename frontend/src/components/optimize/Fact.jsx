import React from "react";

// One labelled figure, in a row of them.
//
// Lifted out of `RunHeader`, where it started, because the step detail page
// needed the same thing and the alternative was a second dialect: this section
// has two pages that both open on "here are the numbers for the thing you just
// clicked", and they were saying it in different visual languages — one a grid
// of labelled figures, the other a single wrapping line of same-sized grey text
// with no labels at all.
//
// The shape is deliberately fixed and small. A label nobody has to decode, the
// figure at the size of a figure, and one line underneath for the qualifier that
// would otherwise be dropped or turned into a footnote — how a number was
// measured, what it is out of, why it is missing. That third line is what keeps
// the row honest: `latency 2.4s` is a claim, `2.4s avg / median 2.5s · max 2.9s`
// is a measurement.
//
// Render these inside a `<dl className="opt-runfacts">`.
export default function Fact({ label, value, sub, title }) {
  return (
    <div className="opt-fact" title={title}>
      <dt>{label}</dt>
      <dd>{value}</dd>
      {sub && <span className="opt-fact-sub">{sub}</span>}
    </div>
  );
}
