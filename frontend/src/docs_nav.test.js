import test from "node:test";
import assert from "node:assert/strict";

import {
  TOOL_PAGES,
  buildDocsNav,
  defaultDocName,
  firstPage,
  resolveDocsRoute,
} from "./docs_nav.js";

// What `GET /docs` actually sends today, spelled out rather than imported: the
// shape belongs to the backend, and a test that built its own input from the
// module under test would agree with itself about a payload neither of them
// receives.
const API = [
  {
    name: "agent-server",
    nav_label: "API reference",
    title: "Agent Server API",
    topic_id: "agent-server",
    topic_label: "Agent Server",
  },
];

// A second topic and a second document in the first, for the ordering tests.
// This is the shape the backend grows into as `docs/` is published; the point of
// testing against it now is that publishing must not require touching this file.
const API_GROWN = [
  API[0],
  {
    name: "agent-server-errors",
    nav_label: "Errors",
    title: "Agent Server Errors",
    topic_id: "agent-server",
    topic_label: "Agent Server",
  },
  {
    name: "routing",
    nav_label: "How routing is scored",
    title: "Routing Optimization",
    topic_id: "optimize",
    topic_label: "Optimization",
  },
];

test("the published documents become topics in the order they arrive", () => {
  const nav = buildDocsNav(API_GROWN, []);

  assert.deepEqual(
    nav.map((t) => [t.id, t.label]),
    [
      ["agent-server", "Agent Server"],
      ["optimize", "Optimization"],
    ]
  );
  assert.deepEqual(nav[0].pages.map((p) => p.name), [
    "agent-server",
    "agent-server-errors",
  ]);
});

test("a tool page lands immediately after the document it names", () => {
  const nav = buildDocsNav(API_GROWN, TOOL_PAGES);

  assert.deepEqual(nav[0].pages.map((p) => p.name), [
    "agent-server",
    "test-server",
    "agent-server-errors",
  ]);
  // Not at the end of the topic, which is where an appending implementation
  // would put it and where nothing would notice until a second document was
  // published.
  assert.equal(nav[0].pages[1].kind, "tool");
  assert.equal(nav[0].pages[0].kind, "doc");
});

test("a tool page whose topic publishes nothing is dropped, not given a topic", () => {
  const orphan = [{ name: "x", navLabel: "X", topicId: "nobody", after: "y" }];

  assert.deepEqual(buildDocsNav(API, orphan), buildDocsNav(API, []));
});

test("a tool page outlives the document it was meant to follow", () => {
  // `agent-server` unpublished, another document still in its topic. The tool
  // still works, so it goes to the end of the topic rather than vanishing with
  // the document that used to precede it.
  const withoutContract = API_GROWN.filter((d) => d.name !== "agent-server");
  const nav = buildDocsNav(withoutContract, TOOL_PAGES);

  assert.deepEqual(nav[0].pages.map((p) => p.name), [
    "agent-server-errors",
    "test-server",
  ]);
});

test("an empty index is an empty tree, not a crash", () => {
  assert.deepEqual(buildDocsNav([], TOOL_PAGES), []);
  assert.deepEqual(buildDocsNav(undefined, undefined), []);
  assert.equal(defaultDocName([]), null);
  assert.equal(defaultDocName(undefined), null);
  assert.equal(firstPage(undefined), null);
});

test("a route resolves to the topic that has to be open for it", () => {
  const nav = buildDocsNav(API_GROWN, TOOL_PAGES);

  const contract = resolveDocsRoute(nav, "agent-server");
  assert.equal(contract.topic.id, "agent-server");
  assert.equal(contract.page.kind, "doc");

  // The tool page resolves to the same topic — which is the whole reason it
  // shares one. Landing on it must not collapse the section it belongs to.
  const tool = resolveDocsRoute(nav, "test-server");
  assert.equal(tool.topic.id, "agent-server");
  assert.equal(tool.page.kind, "tool");

  assert.equal(resolveDocsRoute(nav, "routing").topic.id, "optimize");
});

test("a name nothing publishes resolves to nothing", () => {
  const nav = buildDocsNav(API, TOOL_PAGES);

  // Not an empty topic, not the first page: `null`, so the caller can tell the
  // difference between "no such document" and "the first one".
  assert.equal(resolveDocsRoute(nav, "spec"), null);
  assert.equal(resolveDocsRoute(nav, ""), null);
  assert.equal(resolveDocsRoute([], "agent-server"), null);
});

test("the default document is the first topic's first page", () => {
  const nav = buildDocsNav(API_GROWN, TOOL_PAGES);

  assert.equal(defaultDocName(nav), "agent-server");
  // And it is a document rather than the tool, which is what makes
  // `#/documentation` open on something to read.
  assert.equal(firstPage(nav[0]).kind, "doc");
});

test("every tool page names a topic and a page, so the tree can place it", () => {
  for (const tool of TOOL_PAGES) {
    assert.ok(tool.name);
    assert.ok(tool.navLabel);
    assert.ok(tool.topicId);
  }
});
