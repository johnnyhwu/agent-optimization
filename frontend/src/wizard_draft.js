// The Optimize wizard's answers, kept across a page load.
//
// The wizard is six steps — mode, source, skill, split, settings, review — and
// until now every one of them lived only in React state. Any reload lost all of
// it: a re-login, an accidental refresh, a closed tab. That is the sharp edge
// behind moving the sign-in check outward, because the alternative was telling
// somebody at step six that they had to start again at step one.
//
// **sessionStorage, not localStorage**, for the reason `auth.js` gives about the
// route it parks: the identity-provider redirect comes back to the tab it left,
// and two tabs configuring two runs must not overwrite each other's answers. It
// also means a draft does not outlive the browser session, which is right — a
// wizard reopened next week should open clean rather than on a snapshot of
// whatever was half-decided.
//
// **Versioned and subject-keyed.** The wizard's shape changes with the product,
// and a draft written by an older one is not half-usable — restoring a shape
// the steps no longer read is worse than opening empty, because it looks like
// values somebody chose. Same reasoning `agent_recall.js` gives for dropping
// entries written before an agent was named by two URLs. Keying by subject
// keeps a shared browser from prefilling one person's run from another's.
//
// **Never credentials.** `SECRET_FIELDS` is stripped on the way in rather than
// on the way out: a value never written cannot be read back by whatever else
// can see this origin's storage. Storing a key here would also break the
// promise `services/user_secrets.py` makes about where credentials live.

const KEY = "optimize-wizard-draft";

// Bump when a stored draft can no longer be trusted to fill the current steps.
export const DRAFT_VERSION = 1;

// Dropped before writing. Names rather than a shape check, because a missed
// field here is a credential in browser storage.
export const SECRET_FIELDS = ["agent_api_key", "llm_api_key", "langfuse_secret_key"];

function storageKey(subject) {
  return `${KEY}:${subject || "anon"}`;
}

/** A shallow copy with every credential field removed, at any depth. */
export function withoutSecrets(value) {
  if (Array.isArray(value)) return value.map(withoutSecrets);
  if (value && typeof value === "object") {
    const out = {};
    for (const [k, v] of Object.entries(value)) {
      if (SECRET_FIELDS.includes(k)) continue;
      out[k] = withoutSecrets(v);
    }
    return out;
  }
  return value;
}

/**
 * Keep this draft. Returns whether it was written.
 *
 * Storage can throw — a private window, a full quota, a browser set to block
 * site data — and losing a draft is not a reason to take the wizard down with
 * it, so every failure is swallowed and reported as `false`.
 */
export function saveDraft(subject, draft) {
  if (!draft || typeof draft !== "object") return false;
  try {
    sessionStorage.setItem(
      storageKey(subject),
      JSON.stringify({ version: DRAFT_VERSION, savedAt: Date.now(), draft: withoutSecrets(draft) })
    );
    return true;
  } catch {
    return false;
  }
}

/**
 * The draft to reopen the wizard on, or `null`.
 *
 * `null` for anything not written by this version, unreadable, or not an
 * object. All three are the same answer — open clean — and distinguishing them
 * would only invite a caller to try to salvage one.
 */
export function loadDraft(subject) {
  try {
    const raw = sessionStorage.getItem(storageKey(subject));
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || parsed.version !== DRAFT_VERSION) return null;
    const draft = parsed.draft;
    if (!draft || typeof draft !== "object" || Array.isArray(draft)) return null;
    return draft;
  } catch {
    return null;
  }
}

/**
 * Forget it. Called when the answers become a run, and when the developer
 * presses Start over on the restored-draft notice.
 *
 * There is no third caller and deliberately no `hasDraft` beside this: the
 * wizard restores unconditionally and *says* that it did, which is a notice one
 * component renders rather than a question two of them could answer
 * differently.
 */
export function clearDraft(subject) {
  try {
    sessionStorage.removeItem(storageKey(subject));
    return true;
  } catch {
    return false;
  }
}
