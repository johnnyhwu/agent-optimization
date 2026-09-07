import React, { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api.js";
import Banner, { BannerDetail } from "./ui/Banner.jsx";
import PageHeader from "./ui/PageHeader.jsx";
import Skeleton from "./ui/Skeleton.jsx";
import { findAnchor, renderDoc } from "../doc_render.js";
import { href } from "../useHashRoute.js";

// The reference documentation, rendered from the repository's own markdown.
//
// There is deliberately no second copy of the contract. The file in `docs/` is
// what a reviewer reads and what a developer implements against, and a
// hand-written page beside it would drift — in the worst direction, since the
// on-screen copy is the one somebody builds to while the file is the one that
// gets reviewed.
//
// Most arrivals here are not browsing. They came from a "?" next to a field
// they were filling in, with a specific question, which is why the route
// carries an anchor and why this scrolls to it rather than dropping the reader
// at a table of contents to find the answer a second time.
// The ancestor that actually scrolls. The app shell scrolls its main column
// rather than the window, so `window.scrollTo` is a no-op here and naming the
// class would tie this file to the shell's markup.
function scrollContainer(el) {
  for (let p = el.parentElement; p; p = p.parentElement) {
    if (/(auto|scroll)/.test(getComputedStyle(p).overflowY)) return p;
  }
  return null;
}

export default function Documentation({ doc, anchor }) {
  const [state, setState] = useState({ status: "loading" });
  const bodyRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    api
      .doc(doc)
      .then((r) => !cancelled && setState({ status: "ready", doc: r }))
      .catch((e) => !cancelled && setState({ status: "failed", error: e.message }));
    return () => {
      cancelled = true;
    };
  }, [doc]);

  // `doc` is passed so the document's own fragment links can be rewritten into
  // full routes — see the `link` renderer in doc_render.js.
  const rendered = useMemo(
    () => (state.doc ? renderDoc(state.doc.markdown, doc) : null),
    [state.doc, doc]
  );

  // After the HTML is in the DOM, not before: the element being scrolled to is
  // created by this render.
  //
  // The two cases are not the same scroll, which is the bug this replaces.
  // `scrollIntoView` on the body aligns the *body* with the top of the viewport
  // — so arriving with no anchor scrolled the page 138px and put "Agent Server
  // API" seventy pixels above the window. Opening the docs from the rail landed
  // the reader in the middle of a document with its title gone, which reads
  // exactly like a page that failed to load.
  //
  // With no anchor there is nothing to scroll *to*: the page wants to be at its
  // own top, which means resetting the scroll container rather than moving an
  // element into view. Following two doc links in a row still starts at the
  // top, which is what that scroll was for.
  useEffect(() => {
    if (!rendered || !bodyRef.current) return;
    const id = findAnchor(rendered.headings, anchor);
    const target = id ? bodyRef.current.querySelector(`#${CSS.escape(id)}`) : null;
    if (target) {
      // `.doc-body h2/h3` carry `scroll-margin-top`, so this clears the sticky
      // top bar rather than parking the heading behind it.
      target.scrollIntoView({ block: "start" });
      return;
    }
    scrollContainer(bodyRef.current)?.scrollTo({ top: 0 });
  }, [rendered, anchor]);

  if (state.status === "loading") return <Skeleton variant="text" count={8} />;

  if (state.status === "failed") {
    return (
      <Banner tone="error" className="is-block" title="Could not load this document">
        <BannerDetail>{state.error}</BannerDetail>
      </Banner>
    );
  }

  return (
    <div className="doc-page">
      <PageHeader
        title={state.doc.title}
        subtitle={state.doc.summary}
        // The one action this page has, and the reason it is here rather than
        // at the end of the checklist section: somebody who has finished
        // implementing wants to run it, and should not have to scroll a long
        // reference document to find out that they can.
        primary={
          doc === "agent-server" ? (
            <a className="ui-btn ui-btn-secondary" href={href.docs("test-server")}>
              <span className="ui-btn-label">Test your server</span>
            </a>
          ) : null
        }
      />
      <div className="doc-layout">
        {/* Derived from the document rather than maintained beside it, so the
            two cannot disagree. Second in the source order and placed to the
            right by the grid: on a narrow window it belongs after the thing it
            indexes, not in front of it. */}
        <div
          ref={bodyRef}
          className="doc-body"
          // The markdown is the repository's own file, fetched from this
          // deployment's API, and `renderDoc` escapes raw HTML on the way
          // through. See `doc_render.js`.
          dangerouslySetInnerHTML={{ __html: rendered.html }}
        />
        <nav className="doc-toc" aria-label="On this page">
          <div className="doc-toc-head">On this page</div>
          {rendered.headings.map((h) => (
            <a
              key={h.id}
              href={`#/documentation/${doc}#${h.id}`}
              className={`doc-toc-link doc-toc-h${h.depth}`}
            >
              {h.text}
            </a>
          ))}
        </nav>
      </div>
    </div>
  );
}
