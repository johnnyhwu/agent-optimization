import React from "react";
import { findAnchor } from "../doc_render.js";
import { firstPage } from "../docs_nav.js";
import { href } from "../useHashRoute.js";

// The documentation section's own navigation: topic → page → heading.
//
// **One column, not two.** The obvious shape was a topic/page sidebar on the
// left and the "On this page" contents on the right, which is what this
// replaced. It does not fit: the rail, a 240px sidebar, a measured 78ch of prose
// and a 220px contents column need about 1073px of content width, and a 1280px
// window has 1016px. Keeping both meant hiding the contents below roughly
// 1460px — deleting it for most laptops.
//
// So the headings hang off the page they belong to. The tree that results reads
// as one sentence — you are in this topic, on this page, at this heading — and
// the content column gets the space back.
//
// Real anchors throughout, as in `SideRail`: middle-click, copy-link and Back
// all work without any of our code.

// Which heading is current, if any.
//
// From the route, not from a scroll listener. The anchor is already in the
// address (`useHashRoute.js`), so clicking a heading updates this by navigating
// — no observer, no scroll state, and nothing that only works in a browser and
// therefore cannot be tested here.
function activeHeadingId(headings, anchor) {
  return anchor ? findAnchor(headings, anchor) : null;
}

function Headings({ doc, headings, anchor }) {
  const current = activeHeadingId(headings, anchor);
  return (
    <ul className="docs-nav-headings">
      {headings.map((h) => (
        <li key={h.id}>
          <a
            className={`docs-nav-heading docs-nav-h${h.depth}${
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

export default function DocsNav({ nav, activeTopic, activeDoc, anchor, headings }) {
  return (
    <nav className="docs-nav" aria-label="Documentation">
      <ul className="docs-nav-topics">
        {nav.map((topic) => {
          const open = topic.id === activeTopic?.id;
          const first = firstPage(topic);
          return (
            <li key={topic.id}>
              {/* A topic has no address of its own — this points at its first
                  page. That is what makes "clicking a topic opens its first
                  page" true without a redirect, and a redirect is a history
                  entry Back walks into on the way out. */}
              <a
                className={`docs-nav-topic${open ? " active" : ""}`}
                href={first ? href.docs(first.name) : undefined}
              >
                {topic.label}
              </a>

              {/* Only the topic you are in is expanded. Not a stored preference
                  and not a toggle: a second way to be somewhere is a second
                  thing that can disagree with the address, and the address is
                  already the answer. */}
              {open && (
                <ul className="docs-nav-pages">
                  {topic.pages.map((page) => {
                    const here = page.name === activeDoc;
                    return (
                      <li key={page.name}>
                        <a
                          className={`docs-nav-page${here ? " active" : ""}`}
                          href={href.docs(page.name)}
                          aria-current={here ? "page" : undefined}
                        >
                          {page.navLabel}
                        </a>
                        {/* Headings belong to the page being read, so they
                            appear under it and nowhere else. A tool page has
                            none — it is a form, not a document. */}
                        {here && headings?.length > 0 && (
                          <Headings
                            doc={page.name}
                            headings={headings}
                            anchor={anchor}
                          />
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
