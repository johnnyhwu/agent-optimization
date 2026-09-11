import React from "react";
import { findAnchor } from "../doc_render.js";
import { href } from "../useHashRoute.js";

// "On this page" — the headings of the document being read, in a fixed column
// beside it.
//
// This is the half of the navigation that `DocsNav` could not keep. Its comment
// records why: rail + a 240px sidebar + 78ch of prose + a contents column needs
// about 1450px, and a 1280px laptop does not have it, so the headings were moved
// under the page they belong to in the left column and the right one was
// deleted.
//
// It is back as a **third grid column that only exists where there is room for
// it** — `.docs-shell.has-toc` above the breakpoint in `styles.css`, which is
// also where `.docs-nav-headings` is hidden. One set of headings on screen at
// any width: the right column on a wide screen, the left column's third level
// on a narrow one. Neither is a preference and nothing here measures anything;
// the two are the same links rendered twice and CSS picks.
//
// Real anchors and a current heading read from the route, exactly as in
// `DocsNav` — no scroll listener. The anchor is already in the address
// (`useHashRoute.js`), so clicking a heading updates the highlight by
// navigating, which is also the only version of this that is true in a test.

// The links themselves, shared with the left column so the two levels cannot
// drift into disagreeing about what the headings are or where they point.
export function HeadingLinks({ doc, headings, anchor, className, linkClass, depthClass }) {
  const current = anchor ? findAnchor(headings, anchor) : null;
  return (
    <ul className={className}>
      {headings.map((h) => (
        <li key={h.id}>
          <a
            className={`${linkClass} ${depthClass}${h.depth}${
              h.id === current ? " active" : ""
            }`}
            href={href.docs(doc, h.id)}
            aria-current={h.id === current ? "location" : undefined}
          >
            {h.text}
          </a>
        </li>
      ))}
    </ul>
  );
}

export default function DocsToc({ doc, headings, anchor }) {
  return (
    <nav className="docs-toc" aria-label="On this page">
      <div className="docs-toc-title">On this page</div>
      <HeadingLinks
        doc={doc}
        headings={headings}
        anchor={anchor}
        className="docs-toc-list"
        linkClass="docs-toc-link"
        depthClass="docs-toc-h"
      />
    </nav>
  );
}
