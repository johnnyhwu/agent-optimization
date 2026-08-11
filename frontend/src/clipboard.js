// Copying text to the clipboard, and saying which way it went.
//
// `navigator.clipboard` does not exist on an insecure origin, which is exactly
// how this app is reached in most deployments — an internal host over plain
// http. The Copy button therefore threw on its first line, the throw was
// swallowed, and the button did nothing at all: no "Copied", no error, no
// difference between "it worked" and "this browser will never let it work".
//
// So there are two mechanisms and three outcomes, and the outcome is returned
// rather than logged. The caller needs it: "copied" and "could not copy" want
// different words on screen, and only the second one is worth interrupting
// someone over.
//
// `env` exists so this is testable without a DOM — the fallback is the branch
// most likely to rot, being the one nobody's browser exercises during
// development.

export const COPY_OK = "copied";
export const COPY_FAILED = "failed";

export async function copyText(text, env = {}) {
  const nav = env.navigator ?? globalThis.navigator;
  const doc = env.document ?? globalThis.document;

  if (nav?.clipboard?.writeText) {
    try {
      await nav.clipboard.writeText(text);
      return COPY_OK;
    } catch {
      // Present but refused — permissions policy, or a denied prompt. The
      // selection-based route below is sometimes still allowed, so fall through
      // rather than reporting failure on the strength of one API's opinion.
    }
  }

  // The pre-clipboard-API route: put the text in a field, select it, copy the
  // selection. Deprecated, and the only thing that works over plain http.
  if (!doc?.body) return COPY_FAILED;
  const field = doc.createElement("textarea");
  field.value = text;
  // Off-screen rather than hidden: `display: none` and `visibility: hidden`
  // both make the element unselectable, and a selection is the whole mechanism.
  field.setAttribute("readonly", "");
  field.style.position = "fixed";
  field.style.top = "-1000px";
  field.style.opacity = "0";
  doc.body.appendChild(field);
  try {
    field.select();
    field.setSelectionRange?.(0, text.length);
    return doc.execCommand?.("copy") ? COPY_OK : COPY_FAILED;
  } catch {
    return COPY_FAILED;
  } finally {
    doc.body.removeChild(field);
  }
}
