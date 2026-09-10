// The documentation sidebar's tree, assembled from two lists that cannot be one.
//
// The backend's `GET /docs` is the whitelist of published markdown, and it is
// the only copy — a document published there and missing from a hand-written
// sidebar would simply be invisible, which is the same failure mode the served
// markdown exists to avoid.
//
// But not every page in this section is a document. `test-server` is a tool: it
// sends real questions to a real agent and costs model calls. The backend has no
// reason to know it exists, so it is declared here and spliced in.
//
// Everything below is pure so it can be tested — there is no DOM test renderer
// in this project, and a navigation tree that silently loses a page is exactly
// the kind of bug that would otherwise ship.

// The pages this section has that are not documents.
//
// `after` names the document a tool page follows inside its topic, rather than
// an index: an index would be a number that has to be revised every time the
// backend publishes something ahead of it, and revised in the one file that
// cannot see the change.
export const TOOL_PAGES = [
  {
    name: "test-server",
    navLabel: "Test your server",
    topicId: "agent-server",
    after: "agent-server",
  },
];

// `GET /docs` → the sidebar's topics, in the order the backend declared them.
//
// A tool page whose topic is not published is dropped rather than given a topic
// of its own. The alternative is a heading that appears when a document is
// unpublished, naming a subject with no documentation under it.
export function buildDocsNav(apiDocs, toolPages = TOOL_PAGES) {
  const topics = [];
  const byId = new Map();

  for (const doc of apiDocs || []) {
    let topic = byId.get(doc.topic_id);
    if (!topic) {
      topic = { id: doc.topic_id, label: doc.topic_label, pages: [] };
      byId.set(topic.id, topic);
      topics.push(topic);
    }
    topic.pages.push({
      name: doc.name,
      navLabel: doc.nav_label,
      title: doc.title,
      kind: "doc",
    });
  }

  for (const tool of toolPages || []) {
    const topic = byId.get(tool.topicId);
    if (!topic) continue;
    const page = { name: tool.name, navLabel: tool.navLabel, kind: "tool" };
    const at = topic.pages.findIndex((p) => p.name === tool.after);
    // Not found means the document it was meant to follow is no longer
    // published. The tool still works, so it goes to the end of its topic
    // rather than disappearing with the document.
    if (at === -1) topic.pages.push(page);
    else topic.pages.splice(at + 1, 0, page);
  }

  return topics;
}

// Where a route lands. `null` for a name nothing publishes — the caller sends
// those to `defaultDocName` rather than rendering an empty page.
export function resolveDocsRoute(nav, docName) {
  for (const topic of nav || []) {
    for (const page of topic.pages) {
      if (page.name === docName) return { topic, page };
    }
  }
  return null;
}

// A topic has no address of its own: clicking it opens its first page.
//
// That is deliberate. A topic route would have to redirect to a page, and a
// redirect in the middle of a hash history is a step Back walks into on its way
// out. Pointing the link straight at the page makes "the topic opens on its
// first page" true without anything having to happen at runtime.
export function firstPage(topic) {
  return topic?.pages?.[0] || null;
}

// Where `#/documentation` with no document, or with one nobody publishes, ends
// up. `null` only if the backend published nothing at all.
export function defaultDocName(nav) {
  return firstPage(nav?.[0])?.name || null;
}
