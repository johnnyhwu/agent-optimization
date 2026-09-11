import React, { useEffect, useRef, useState } from "react";
import PageHeader from "./ui/PageHeader.jsx";
import Button from "./ui/Button.jsx";
import { IconCheck, IconCopy } from "./icons.jsx";
import { useToast } from "./Toast.jsx";
import { COPY_OK, copyText } from "../clipboard.js";
import { findAnchor } from "../doc_render.js";

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
//
// The markdown is fetched and rendered by `DocsSection`, which needs the
// headings to draw the navigation. This renders what it is handed.
// The ancestor that actually scrolls. The app shell scrolls its main column
// rather than the window, so `window.scrollTo` is a no-op here and naming the
// class would tie this file to the shell's markup.
function scrollContainer(el) {
  for (let p = el.parentElement; p; p = p.parentElement) {
    if (/(auto|scroll)/.test(getComputedStyle(p).overflowY)) return p;
  }
  return null;
}

export default function Documentation({ title, summary, rendered, anchor, markdown }) {
  const bodyRef = useRef(null);
  const toast = useToast();
  const [copied, setCopied] = useState(false);

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

  // Answered in two places, as the format examples' copy button is: the label
  // flip is where the eyes already are, and the toast is the only one of the two
  // that can report a failure — there is no label state for "this browser will
  // never let me".
  //
  // `copyText` rather than `navigator.clipboard`: most deployments of this are
  // reached over plain http, where the clipboard API does not exist at all, and
  // it carries the selection-based fallback that does work there.
  async function copy() {
    if ((await copyText(markdown)) === COPY_OK) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
      toast.success("Page copied as Markdown");
      return;
    }
    toast.error("Could not reach the clipboard — select the text and copy it instead.");
  }

  return (
    <>
      {/* The one action a reference page has: the document, as the file it is.
          What is copied is the markdown the backend served — the repository's
          own file, byte for byte — and not this page's HTML or anything derived
          back out of it, because the thing somebody wants in their editor or
          their prompt is the source everybody else implements against. */}
      <PageHeader
        title={title}
        subtitle={summary}
        primary={
          markdown ? (
            <Button
              variant="secondary"
              icon={copied ? <IconCheck size={14} /> : <IconCopy size={14} />}
              onClick={copy}
            >
              {copied ? "Copied" : "Copy page"}
            </Button>
          ) : undefined
        }
      />
      <div
        ref={bodyRef}
        className="doc-body"
        // The markdown is the repository's own file, fetched from this
        // deployment's API, and `renderDoc` escapes raw HTML on the way
        // through. See `doc_render.js`.
        dangerouslySetInnerHTML={{ __html: rendered.html }}
      />
    </>
  );
}
