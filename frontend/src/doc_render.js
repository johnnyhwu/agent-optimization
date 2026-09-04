// Turning a repository markdown file into a page, and finding a section in it.
//
// The document served here is the one the repository reviews (`docs/*.md`), so
// this module's job is to change as little about it as possible: give every
// heading a stable id so a link can point at one, build a table of contents from
// what is actually in the file, and refuse to emit raw HTML.
//
// Raw HTML is escaped even though the source is our own file. The content is
// trusted today; the escape costs nothing and means a future edit to a markdown
// file cannot put markup into the application shell.

import { Marked } from "marked";

// GitHub-style: lowercase, drop anything that is not a word character, space or
// hyphen, then hyphenate. The same rule the file's own table of contents was
// written against, so its links work without being rewritten.
export function slugify(text) {
  return String(text)
    .trim()
    .toLowerCase()
    .replace(/[^\w\s-]/g, "")
    .replace(/\s+/g, "-");
}

/**
 * Which heading a link is asking for.
 *
 * An exact id wins. Otherwise a heading whose slug *ends* with the anchor does —
 * so `chat-endpoint` finds `3-chat-endpoint`, and keeps finding it when the
 * document is renumbered. A "?" beside a form field is written once and read for
 * years; making it depend on a section number would break it silently the first
 * time somebody inserts a section.
 */
export function findAnchor(headings, anchor) {
  if (!anchor) return "";
  const wanted = slugify(anchor);
  const exact = headings.find((h) => h.id === wanted);
  if (exact) return exact.id;
  const suffix = headings.find((h) => h.id.endsWith(`-${wanted}`));
  return suffix ? suffix.id : "";
}

/**
 * `{ html, headings }` for one markdown document.
 *
 * `headings` is every `##`/`###` in order, which is what the page's contents
 * column is built from — derived rather than maintained, so it cannot disagree
 * with the document.
 */
export function renderDoc(markdown) {
  const headings = [];
  const seen = new Map();
  // The page prints the document's title itself, from the API. Rendering the
  // file's own `# Title` as well put the same words on screen twice, one above
  // the other, which reads as a mistake rather than as a heading.
  let droppedTitle = false;

  const marked = new Marked({ gfm: true, breaks: false });
  marked.use({
    renderer: {
      heading({ tokens, depth }) {
        const text = this.parser.parseInline(tokens);
        const plain = text.replace(/<[^>]*>/g, "");
        if (depth === 1 && !droppedTitle) {
          droppedTitle = true;
          return "";
        }
        let id = slugify(plain);
        // Two identical headings would otherwise share an id, and a link to the
        // second would land on the first — which looks like a link to the right
        // place showing the wrong content.
        if (seen.has(id)) {
          const n = seen.get(id) + 1;
          seen.set(id, n);
          id = `${id}-${n}`;
        } else {
          seen.set(id, 0);
        }
        if (depth === 2 || depth === 3) headings.push({ id, text: plain, depth });
        return `<h${depth} id="${id}">${text}</h${depth}>\n`;
      },
      // Never pass markup through. See the note at the top of the file.
      html({ text }) {
        return escapeHtml(text);
      },
    },
  });

  return { html: marked.parse(markdown), headings };
}

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}
