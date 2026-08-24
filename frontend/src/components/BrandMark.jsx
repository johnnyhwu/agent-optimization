import React from "react";

// The product mark: a pen nib knocked out of an indigo tile. A skill is a
// document somebody wrote, so the mark is the thing you write with — and the
// nib keeps its two functional details (the vent hole and the slit) because
// those are what stop it reading as a generic triangle.
//
// The nib is the negative space rather than a painted shape, and the tile is
// the product's accent rather than a colour of its own: at 16px a browser tab
// resolves one solid tile and one hole, which is the most a favicon can carry.
// The predecessor was a brass gradient on a near-black tile, which averaged out
// to a tan smear at that size and sat off the palette everywhere else.
//
// Upright, and every coordinate on a half-pixel of the 32-unit box: the old
// mark was rotated -6°, which bought a jauntiness nobody can see below 32px at
// the cost of soft edges in the tab strip.
//
// The same drawing is the favicon, hand-encoded as a data URI in index.html.
// Change one and change the other, or the tab and the rail stop matching.
export default function BrandMark({ size = 28, ...rest }) {
  return (
    // Decorative by default: every place this appears sits next to the product
    // name, or inside a link that carries the name itself. A label here would
    // announce it twice.
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" {...rest}>
      {/* --accent, as a literal. An SVG in a data URI cannot read a custom
          property, and the favicon and this have to be the same drawing. */}
      <rect width="32" height="32" rx="8" fill="#6366f1" />
      {/* Body, vent hole and slit as one even-odd path, so the holes are
          actually holes — here they have to be, because what shows through
          them is the tile itself.
          The proportions are set by the 16px case, not the large one, and by
          rasterising it rather than by eye: a nib in a 22px live area with a
          3.0 vent looked correct at 28px and went to a white speck with a
          closed-up dimple in a tab strip. This one spans 15.2 x 20.4 of the
          tile with a 3.4 vent, which is the largest the shoulders can get
          before they crowd the tile's corners, and the vent is still an open
          hole at four pixels across. */}
      <path
        fill="#ffffff"
        fillRule="evenodd"
        d="M8.4 5.8 H23.6 L21 17.4 L16 26.2 L11 17.4 Z
           M19.4 11.2 A3.4 3.4 0 1 1 12.6 11.2 A3.4 3.4 0 1 1 19.4 11.2 Z
           M15 16.4 H17 L16.45 24.8 H15.55 Z"
      />
    </svg>
  );
}
