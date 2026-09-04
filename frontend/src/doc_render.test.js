import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { findAnchor, renderDoc, slugify } from "./doc_render.js";

describe("slugify", () => {
  it("matches the rule the document's own contents links were written against", () => {
    assert.equal(slugify("3. Chat endpoint"), "3-chat-endpoint");
    assert.equal(slugify("What each endpoint unlocks"), "what-each-endpoint-unlocks");
    assert.equal(slugify("Errors, and what each one causes"), "errors-and-what-each-one-causes");
  });
});

describe("findAnchor", () => {
  const headings = [
    { id: "1-what-each-endpoint-unlocks", text: "…", depth: 2 },
    { id: "3-chat-endpoint", text: "…", depth: 2 },
    { id: "4-skills-endpoint", text: "…", depth: 2 },
  ];

  it("finds a section by name, without its number", () => {
    // The "?" beside a form field is written once and read for years. Tying it
    // to a section number would break it silently the first time somebody
    // inserted a section above.
    assert.equal(findAnchor(headings, "chat-endpoint"), "3-chat-endpoint");
    assert.equal(findAnchor(headings, "skills-endpoint"), "4-skills-endpoint");
  });

  it("prefers an exact id", () => {
    assert.equal(findAnchor(headings, "3-chat-endpoint"), "3-chat-endpoint");
  });

  it("finds a section whose title says more than its name", () => {
    // "8. Authentication (optional)" ends in neither its number nor its name,
    // so a suffix match misses it and the "?" link lands silently at the top of
    // the document — which reads as a link that works.
    const titled = [
      { id: "8-authentication-optional", text: "…", depth: 2 },
      { id: "9-errors-and-what-each-one-causes", text: "…", depth: 2 },
    ];
    assert.equal(findAnchor(titled, "authentication"), "8-authentication-optional");
    assert.equal(findAnchor(titled, "errors"), "9-errors-and-what-each-one-causes");
  });

  it("prefers a whole-name match over one that merely starts the same way", () => {
    // The prefix rule is a last resort, which is what keeps it safe: a document
    // with a section actually called "Skills" still answers `skills` with that
    // one and not with "Skills endpoint".
    const both = [
      { id: "4-skills-endpoint", text: "…", depth: 2 },
      { id: "5-skills", text: "…", depth: 2 },
    ];
    assert.equal(findAnchor(both, "skills"), "5-skills");
  });

  it("returns nothing for a section that is not there", () => {
    assert.equal(findAnchor(headings, "authentication"), "");
    assert.equal(findAnchor(headings, ""), "");
  });
});

describe("renderDoc", () => {
  it("rewrites the document's own fragment links into full routes", () => {
    // A bare `#3-chat-endpoint` replaces the whole hash route, parses as no
    // known section, falls through to evaluation and is rewritten to
    // `#/evaluation` — so the document's own contents list would throw the
    // reader off the page it points into.
    const { html } = renderDoc("[Chat endpoint](#3-chat-endpoint)\n", "agent-server");
    assert.match(html, /href="#\/documentation\/agent-server#3-chat-endpoint"/);
  });

  it("leaves links that go somewhere else alone", () => {
    const { html } = renderDoc("[the spec](https://example.com/x)\n");
    assert.match(html, /href="https:\/\/example.com\/x"/);
    const relative = renderDoc("[a file](./other.md)\n");
    assert.match(relative.html, /href="\.\/other.md"/);
  });

  it("gives every heading an id a link can point at", () => {
    const { html, headings } = renderDoc("## 3. Chat endpoint\n\ntext\n");
    assert.match(html, /<h2 id="3-chat-endpoint">/);
    assert.deepEqual(headings.map((h) => h.id), ["3-chat-endpoint"]);
  });

  it("collects h2 and h3 in order, and leaves the title out", () => {
    // The contents column is derived from the document rather than maintained
    // beside it, so the two cannot disagree.
    const { headings } = renderDoc("# Title\n\n## One\n\n### One a\n\n## Two\n");
    assert.deepEqual(headings.map((h) => h.text), ["One", "One a", "Two"]);
    assert.deepEqual(headings.map((h) => h.depth), [2, 3, 2]);
  });

  it("keeps two identical headings apart", () => {
    // Sharing an id sends a link to the first one — which looks like a link to
    // the right place showing the wrong content.
    const { headings } = renderDoc("## Response\n\n## Response\n");
    assert.deepEqual(headings.map((h) => h.id), ["response", "response-1"]);
  });

  it("escapes raw HTML rather than passing it through", () => {
    // The source is our own file today. The escape costs nothing and means a
    // future edit cannot put markup into the application shell.
    const { html } = renderDoc("<script>alert(1)</script>\n");
    assert.doesNotMatch(html, /<script>/);
    assert.match(html, /&lt;script&gt;/);
  });

  it("leaves markup inside a code fence looking like markup", () => {
    // The contract document quotes an HTML comment as something implementers
    // will see in their logs; it has to read as that comment.
    const { html } = renderDoc("```\n<!-- probe-8f3a: ignore this -->\n```\n");
    assert.match(html, /probe-8f3a/);
    assert.match(html, /&lt;!-- probe-8f3a/);
  });

  it("renders tables, which the contract is mostly made of", () => {
    const { html } = renderDoc("| a | b |\n|---|---|\n| 1 | 2 |\n");
    assert.match(html, /<table>/);
  });
});
