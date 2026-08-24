// Pure helpers for the agent workspace the playground edits.
//
// The editor works on a **full copy** of the agent's skill files, and only at
// send time is that copy turned into what actually travels. What travels is the
// **complete** file set, replacing the agent's directory for that one call
// rather than patching it — because only replacement can express deleting a
// file, which is a legitimate experiment ("does it still answer without this
// reference?"). That is the contract with the agent server, not a detail of
// this file.

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
    files: attempt.edited_skill_files?.length || 0,
  };
}
