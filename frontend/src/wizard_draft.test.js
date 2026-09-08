import test, { beforeEach } from "node:test";
import assert from "node:assert/strict";
import {
  DRAFT_VERSION,
  SECRET_FIELDS,
  clearDraft,
  hasDraft,
  loadDraft,
  saveDraft,
  withoutSecrets,
} from "./wizard_draft.js";

// node --test has no DOM. A Map-backed stand-in is enough: the module only ever
// calls getItem/setItem/removeItem, and the cases worth testing are what it does
// with what comes back — including when the call throws.
function installStorage(impl) {
  globalThis.sessionStorage = impl;
}

function memoryStorage() {
  const map = new Map();
  return {
    map,
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: (k) => map.delete(k),
  };
}

const throwingStorage = () => ({
  getItem() {
    throw new Error("blocked");
  },
  setItem() {
    throw new Error("quota");
  },
  removeItem() {
    throw new Error("blocked");
  },
});

beforeEach(() => installStorage(memoryStorage()));

test("a draft survives a round trip", () => {
  saveDraft("alice", { stepIndex: 3, mode: "routing", skill: "billing" });
  assert.deepEqual(loadDraft("alice"), {
    stepIndex: 3,
    mode: "routing",
    skill: "billing",
  });
});

test("nothing saved means nothing to restore", () => {
  assert.equal(loadDraft("alice"), null);
  assert.equal(hasDraft("alice"), false);
});

test("a draft is scoped to its subject", () => {
  // A shared browser must not prefill one person's run from another's answers.
  saveDraft("alice", { mode: "routing" });
  assert.equal(loadDraft("bob"), null);
  assert.deepEqual(loadDraft("alice"), { mode: "routing" });
});

test("clearing forgets it", () => {
  saveDraft("alice", { mode: "isolated" });
  clearDraft("alice");
  assert.equal(loadDraft("alice"), null);
});

test("a draft from an older wizard is dropped, not half-restored", () => {
  // Restoring a shape the steps no longer read is worse than opening empty:
  // it looks like values somebody chose.
  const storage = memoryStorage();
  installStorage(storage);
  storage.setItem(
    "optimize-wizard-draft:alice",
    JSON.stringify({ version: DRAFT_VERSION - 1, draft: { mode: "routing" } })
  );
  assert.equal(loadDraft("alice"), null);
});

test("unreadable storage contents are dropped", () => {
  const storage = memoryStorage();
  installStorage(storage);
  for (const junk of ["{not json", "null", '"a string"', "[1,2]", "{}"]) {
    storage.setItem("optimize-wizard-draft:alice", junk);
    assert.equal(loadDraft("alice"), null, junk);
  }
});

// --- credentials never reach storage --------------------------------------

test("a credential is stripped before it is written", () => {
  saveDraft("alice", {
    mode: "routing",
    secrets: { agent_api_key: "sk-42", llm_api_key: "sk-99" },
  });
  const raw = globalThis.sessionStorage.getItem("optimize-wizard-draft:alice");
  assert.ok(!raw.includes("sk-42"), "an agent key reached browser storage");
  assert.ok(!raw.includes("sk-99"), "an LLM key reached browser storage");
  assert.deepEqual(loadDraft("alice"), { mode: "routing", secrets: {} });
});

test("stripping reaches every depth and through arrays", () => {
  const cleaned = withoutSecrets({
    a: { b: [{ agent_api_key: "sk-1", keep: 1 }] },
    langfuse_secret_key: "sk-2",
  });
  assert.deepEqual(cleaned, { a: { b: [{ keep: 1 }] } });
});

test("every named secret field is actually stripped", () => {
  // The list is the mechanism, so a field added to it without being handled
  // would be a credential in storage.
  for (const field of SECRET_FIELDS) {
    assert.deepEqual(withoutSecrets({ [field]: "x", keep: 1 }), { keep: 1 }, field);
  }
});

test("stripping leaves non-objects alone", () => {
  assert.equal(withoutSecrets("x"), "x");
  assert.equal(withoutSecrets(7), 7);
  assert.equal(withoutSecrets(null), null);
  assert.equal(withoutSecrets(undefined), undefined);
});

// --- storage that refuses ---------------------------------------------------

test("storage that throws does not take the wizard down", () => {
  // A private window, a full quota, a browser set to block site data. Losing a
  // draft is not a reason to break the page.
  installStorage(throwingStorage());
  assert.equal(saveDraft("alice", { mode: "routing" }), false);
  assert.equal(loadDraft("alice"), null);
  assert.equal(clearDraft("alice"), false);
  assert.equal(hasDraft("alice"), false);
});

test("a non-object draft is refused rather than stored", () => {
  assert.equal(saveDraft("alice", null), false);
  assert.equal(saveDraft("alice", "string"), false);
  assert.equal(loadDraft("alice"), null);
});
