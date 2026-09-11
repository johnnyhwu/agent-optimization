import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { buildDocsNav, defaultDocName, resolveDocsRoute } from "../docs_nav.js";
import { renderDoc } from "../doc_render.js";
import { href, replace } from "../useHashRoute.js";
import Banner, { BannerDetail } from "./ui/Banner.jsx";
import Documentation from "./Documentation.jsx";
import DocsNav from "./DocsNav.jsx";
import DocsToc from "./DocsToc.jsx";
import ServerCheck from "./ServerCheck.jsx";
import Skeleton from "./ui/Skeleton.jsx";

// The documentation section: a navigation column and the page it points at.
//
// This owns both fetches — the index that the sidebar is drawn from, and the
// markdown of the document being read — because the sidebar needs the rendered
// document's headings to show its third level. Leaving the markdown inside
// `Documentation` would mean the two halves of one screen each fetching, and
// the half that draws the tree fetching a document to draw a list.
//
// It also means the markdown is fetched once per document rather than once per
// click. `App` used to key `Documentation` on the anchor as well as the name, so
// following a link within the same page threw the document away and asked for it
// again — a network round trip to scroll.

export default function DocsSection({ route }) {
  const [index, setIndex] = useState({ status: "loading" });
  const [doc, setDoc] = useState({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    api
      .docsIndex()
      .then((r) => !cancelled && setIndex({ status: "ready", docs: r.docs }))
      .catch((e) => !cancelled && setIndex({ status: "failed", error: e.message }));
    return () => {
      cancelled = true;
    };
  }, []);

  const nav = useMemo(
    () => (index.docs ? buildDocsNav(index.docs) : []),
    [index.docs]
  );
  const here = resolveDocsRoute(nav, route.doc);
  const isDoc = here?.page?.kind === "doc";

  // A name nothing publishes goes to the first page — replaced rather than
  // pushed, because a corrected address must not become a Back target. Waits
  // for the index: before it arrives, every name looks unpublished.
  useEffect(() => {
    if (index.status !== "ready" || here) return;
    const fallback = defaultDocName(nav);
    if (fallback) replace(href.docs(fallback));
  }, [index.status, here, nav]);

  // Only for a document. The tool page has no markdown, and asking for it would
  // be a 404 in the network log of a page that is working correctly.
  useEffect(() => {
    if (!isDoc) return undefined;
    let cancelled = false;
    setDoc({ status: "loading" });
    api
      .doc(route.doc)
      .then((r) => !cancelled && setDoc({ status: "ready", doc: r }))
      .catch((e) => !cancelled && setDoc({ status: "failed", error: e.message }));
    return () => {
      cancelled = true;
    };
  }, [isDoc, route.doc]);

  // `route.doc` is passed so the document's own fragment links can be rewritten
  // into full routes — see the `link` renderer in doc_render.js.
  const rendered = useMemo(
    () => (isDoc && doc.doc ? renderDoc(doc.doc.markdown, route.doc) : null),
    [isDoc, doc.doc, route.doc]
  );

  if (index.status === "failed") {
    return (
      <Banner tone="error" className="is-block" title="Could not load the documentation">
        <BannerDetail>{index.error}</BannerDetail>
      </Banner>
    );
  }
  if (index.status === "loading") return <Skeleton variant="text" count={8} />;

  // The contents column exists only for a document that has headings — a tool
  // page is a form, and an empty third column would still take its width out of
  // the prose. `has-toc` is what opens the grid's third track, so the two
  // decisions are one.
  const toc = isDoc && rendered?.headings?.length > 0 ? rendered.headings : null;

  return (
    <div className={`docs-shell${toc ? " has-toc" : ""}`}>
      <DocsNav
        nav={nav}
        activeTopic={here?.topic}
        activeDoc={here?.page?.name}
        anchor={route.anchor}
        headings={rendered?.headings}
      />
      <div className="docs-content">
        {/* The checker shares the section because it is the same errand — "what
            does my server have to do, and does it?" — which is also why it is a
            page under the same topic rather than a button on the document. */}
        {here && !isDoc && <ServerCheck />}
        {isDoc && doc.status === "failed" && (
          <Banner tone="error" className="is-block" title="Could not load this document">
            <BannerDetail>{doc.error}</BannerDetail>
          </Banner>
        )}
        {isDoc && doc.status === "loading" && <Skeleton variant="text" count={8} />}
        {isDoc && rendered && (
          <Documentation
            title={doc.doc.title}
            summary={doc.doc.summary}
            rendered={rendered}
            anchor={route.anchor}
            // The markdown this page was rendered from, for the Copy button in
            // its header. Already here — it is what `renderDoc` above was given
            // — so the copy is the file itself rather than anything this code
            // reconstructed from the HTML.
            markdown={doc.doc.markdown}
          />
        )}
      </div>
      {toc && <DocsToc doc={route.doc} headings={toc} anchor={route.anchor} />}
    </div>
  );
}
