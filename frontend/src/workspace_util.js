// Pure helpers for the agent workspace the playground edits (§10.2).
//
// The editor works on a **full copy** of the agent's config and skill files, and
// only at send time is that copy turned into what actually travels. The two
// halves are turned into different things, and that asymmetry is the contract
// with the agent server, not a detail of this file:
//
//   * config -> a **sparse** overlay (`diffConfig`), deep-merged on the agent
//     server. It has to be sparse: the snapshot arrived with the agent's own
//     secrets stripped out, so sending the whole object back would tell the
//     agent server its API key is now absent.
//   * skills -> the **complete** file set, replacing the agent's directory for
//     that one call. Only replacement can express deleting a file, which is a
//     legitimate experiment ("does it still answer without this reference?").

export function isLeaf(value) {
  // An empty object is a leaf: there is nothing under it to render a field for,
  // so it is edited as JSON like an array is.
  return (
    value === null ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    Object.keys(value).length === 0
  );
}

// Every editable value in the config, as {path, key, parent, value} — the flat
// list the form renders, with the hierarchy preserved in `path` so a field can
// be labelled by where it lives ("model" under agents.defaults).
export function flattenLeaves(node, prefix = "") {
  if (!node || typeof node !== "object") return [];
  return Object.entries(node).flatMap(([key, value]) => {
    const path = prefix ? `${prefix}.${key}` : key;
    if (isLeaf(value)) {
      return [{ path, key, parent: prefix, value }];
    }
    return flattenLeaves(value, path);
  });
}

export function getAt(node, path) {
  return path.split(".").reduce((acc, part) => (acc == null ? acc : acc[part]), node);
}

// A copy of `node` with `path` set to `value`. Copies rather than mutates so
// React sees a new object and the caller's snapshot stays pristine — the
// snapshot is what "revert" restores from.
export function setAt(node, path, value) {
  const [head, ...rest] = path.split(".");
  const base = { ...(node || {}) };
  base[head] = rest.length ? setAt(base[head] || {}, rest.join("."), value) : value;
  return base;
}

// The sparse overlay: only the leaves that differ from the snapshot, nested as
// the agent server expects them.
export function diffConfig(base, edited) {
  let out = null;
  flattenLeaves(edited).forEach(({ path, value }) => {
    if (!sameValue(getAt(base, path), value)) {
      out = setAt(out || {}, path, value);
    }
  });
  return out;
}

// Config values are JSON, so a structural comparison is the honest one: two
// equal arrays are not `===`, and treating them as different would report an
// edit nobody made.
export function sameValue(a, b) {
  if (a === b) return true;
  if (typeof a !== typeof b) return false;
  if (a && b && typeof a === "object") return JSON.stringify(a) === JSON.stringify(b);
  return false;
}

// Which files the developer touched, for the summary line. Deletions count:
// removing a reference file is an edit with a result, not an absence.
export function editedFiles(base, edited) {
  const changed = Object.keys(edited).filter((p) => base[p] !== edited[p]);
  const deleted = Object.keys(base).filter((p) => !(p in edited));
  return [...changed, ...deleted].sort();
}

export function sameSkills(base, edited) {
  return editedFiles(base, edited).length === 0;
}

// Redacted paths are matched by prefix as well as exactly: the agent server
// removes a leaf, but a whole redacted subtree is just as plausible.
export function isRedacted(path, redactedPaths) {
  return (redactedPaths || []).some((p) => p === path || path.startsWith(`${p}.`));
}

// Drop anything sitting on a redacted path. The contract says the agent server
// removes its secrets before serving the workspace, but a server that masks one
// as "***" instead would otherwise let that mask be sent back as an override —
// setting the agent's real API key to three asterisks. Cheap insurance against
// somebody else's shortcut.
export function stripRedacted(config, redactedPaths) {
  if (!config || !redactedPaths?.length) return config;
  let out = null;
  flattenLeaves(config).forEach(({ path, value }) => {
    if (!isRedacted(path, redactedPaths)) out = setAt(out || {}, path, value);
  });
  return out;
}

// The top-level directory of a skill file, i.e. the skill it belongs to. Used
// only for grouping the file list, so a file sitting loose at the root is its
// own group rather than an error.
export function skillOf(path) {
  const slash = path.indexOf("/");
  return slash === -1 ? "" : path.slice(0, slash);
}

// How much of the agent's workspace an attempt replaced. The two call sites
// word it differently — a list row has no room for a sentence — but what counts
// as an override is decided once, here.
export function overrideCounts(attempt) {
  return {
    configs: attempt.config_overrides?.length || 0,
    files: attempt.edited_skill_files?.length || 0,
  };
}
