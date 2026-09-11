import React from "react";
import { firstPage } from "../docs_nav.js";
import { href } from "../useHashRoute.js";
import { HeadingLinks } from "./DocsToc.jsx";

// The documentation section's own navigation: topic → page → heading.
//
// **The third level is here only where a contents column does not fit.** The
// obvious shape is a topic/page sidebar on the left and "On this page" on the
// right, and on a 1280px window it does not fit: the rail, a 240px sidebar, a
// measured 78ch of prose and a contents column want about 1450px. So there are
// two renderings of one set of headings and CSS picks between them — above the
// breakpoint `DocsToc` draws them in a column of its own and `.docs-nav-headings`
// is hidden; below it they hang off the page they belong to, here, and the
// tree reads as one sentence: you are in this topic, on this page, at this
// heading. The links themselves are `HeadingLinks`, shared with that column so
// the two cannot disagree.
//
// Real anchors throughout, as in `SideRail`: middle-click, copy-link and Back
// all work without any of our code.

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
                          <HeadingLinks
                            doc={page.name}
                            headings={headings}
                            anchor={anchor}
                            className="docs-nav-headings"
                            linkClass="docs-nav-heading"
                            depthClass="docs-nav-h"
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
