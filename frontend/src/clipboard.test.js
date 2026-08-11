import { strict as assert } from "node:assert";
import { test } from "node:test";

import { COPY_FAILED, COPY_OK, copyText } from "./clipboard.js";

// A DOM stub with just the parts the fallback touches. `copied` records what
// execCommand would have put on the clipboard, so the tests can assert the text
// actually made it into the selected field rather than only that a branch ran.
function fakeDom({ execResult = true, throwOnSelect = false } = {}) {
  const state = { copied: null, appended: 0, removed: 0 };
  const body = {
    appendChild: (el) => {
      state.appended += 1;
      state.field = el;
    },
    removeChild: () => {
      state.removed += 1;
    },
  };
  const document = {
    body,
    createElement: () => ({
      value: "",
      style: {},
      setAttribute() {},
      select() {
        if (throwOnSelect) throw new Error("no selection allowed");
      },
      setSelectionRange() {},
    }),
    execCommand: () => {
      if (execResult) state.copied = state.field.value;
      return execResult;
    },
  };
  return { document, state };
}

test("uses the clipboard API when it is available", async () => {
  let written = null;
  const navigator = { clipboard: { writeText: async (t) => { written = t; } } };
  const { document, state } = fakeDom();

  assert.equal(await copyText("hello", { navigator, document }), COPY_OK);
  assert.equal(written, "hello");
  // The fallback must not also run — two copies of one press is one too many.
  assert.equal(state.appended, 0);
});

test("falls back to a selection when there is no clipboard API", async () => {
  // What an insecure origin looks like: navigator exists, navigator.clipboard
  // does not. This is the case the empty catch used to swallow.
  const { document, state } = fakeDom();

  assert.equal(await copyText("some snippet", { navigator: {}, document }), COPY_OK);
  assert.equal(state.copied, "some snippet");
  assert.equal(state.removed, 1, "the scratch field is always cleaned up");
});

test("falls back when the clipboard API is present but rejects", async () => {
  const navigator = { clipboard: { writeText: async () => { throw new Error("denied"); } } };
  const { document, state } = fakeDom();

  assert.equal(await copyText("x", { navigator, document }), COPY_OK);
  assert.equal(state.copied, "x");
});

test("reports failure when both routes are unavailable", async () => {
  const { document } = fakeDom({ execResult: false });
  assert.equal(await copyText("x", { navigator: {}, document }), COPY_FAILED);
});

test("reports failure — and still cleans up — when selection throws", async () => {
  const { document, state } = fakeDom({ throwOnSelect: true });
  assert.equal(await copyText("x", { navigator: {}, document }), COPY_FAILED);
  assert.equal(state.removed, 1);
});

test("reports failure when there is no document at all", async () => {
  assert.equal(await copyText("x", { navigator: {}, document: undefined }), COPY_FAILED);
});
