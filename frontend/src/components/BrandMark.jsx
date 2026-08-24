import React from "react";

// The product mark: a brass pen nib on a dark tile. A skill is a document
// somebody wrote, so the mark is the thing you write with — and the nib keeps
// its two functional details (the vent hole and the slit) because those are
// what stop it reading as a generic triangle.
//
// The same drawing is the favicon, hand-encoded as a data URI in index.html.
// Change one and change the other, or the tab and the rail stop matching.
export default function BrandMark({ size = 28, ...rest }) {
  // The gradient lives in the document, so two of these on one page would
  // otherwise declare the same id twice and the second would win.
  const gid = React.useId();
  return (
    // Decorative by default: every place this appears sits next to the product
    // name, or inside a link that carries the name itself. A label here would
    // announce it twice.
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" {...rest}>
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2=".8" y2="1">
          <stop offset="0" stopColor="#f3d8a4" />
          <stop offset=".5" stopColor="#cfa25c" />
          <stop offset="1" stopColor="#a2782f" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="8" fill="#16151d" />
      <g transform="rotate(-6 16 16)">
        {/* Body, vent hole and slit as one even-odd path, so the holes are
            actually holes rather than shapes painted in the tile's colour —
            which would break the moment the tile stops being flat.
            The proportions are set by the 16px case, not the large one: a
            smaller nib and a smaller vent both survived 64px happily and went
            to an unreadable speck in a browser tab. */}
        <path
          fill={`url(#${gid})`}
          fillRule="evenodd"
          d="M7.8 5.6 H24.2 L21.2 17.6 L16 28 L10.8 17.6 Z
             M19.1 11.4 A3.1 3.1 0 1 1 12.9 11.4 A3.1 3.1 0 1 1 19.1 11.4 Z
             M14.85 15.4 H17.15 L16.55 26 H15.45 Z"
        />
      </g>
    </svg>
  );
}
