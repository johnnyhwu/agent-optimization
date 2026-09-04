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

  const rendered = useMemo(
    () => (state.doc ? renderDoc(state.doc.markdown) : null),
    [state.doc]
  );

  // After the HTML is in the DOM, not before: the element being scrolled to is
  // created by this render.
  useEffect(() => {
    if (!rendered || !bodyRef.current) return;
    const id = findAnchor(rendered.headings, anchor);
    const target = id ? bodyRef.current.querySelector(`#${CSS.escape(id)}`) : null;
    // Top of the document when no anchor was asked for, so following two links
    // in a row does not leave the second one scrolled to where the first was.
    (target || bodyRef.current).scrollIntoView({ block: "start" });
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
