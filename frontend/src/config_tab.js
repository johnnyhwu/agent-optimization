// Which tab the eval set's Settings dialog opens on.
//
// General is the answer nearly every time: it is what "Settings" means, and it
// is what the developer came for. The exception is a set nobody has ever looked
// at the grading of — the judge prompt decides what counts as a right answer,
// and a set graded by criteria no owner has read is the one case worth
// interrupting the expected destination for. Opening on Judging *is* the review:
// closing the dialog marks it read, so this fires once per set and then stops.
//
// It lives here rather than at the call sites because there are two of them —
// the card in the grid and the set's own page — and they already disagreed once,
// the page opening on Judging every time while the card followed this rule.
export function initialConfigTab(evalSet) {
  return evalSet?.judge_prompt?.reviewed_at ? "general" : "judging";
}

// Whether the versioned PATCH has anything to say.
//
// `PATCH /eval-sets/{id}` bumps `version` unconditionally, and the dialog used
// to send it on every Save. So opening Settings, reading the judge prompt and
// pressing Save advanced the number — which is not a private counter: it is the
// value every other loaded copy of this card is holding, and the thing a 409
// compares against. A version that moves without an edit turns "someone else
// changed this" into a message people learn to click past.
//
// Compared field by field rather than by a dirty flag on the form, because the
// judge prompt textareas are *prefilled with the effective prompt* — the server
// resolves the set's override against the shipped default before sending it — so
// a set that has never overridden anything arrives with text in the box that
// must not count as having been written there.
export function evalSetEdits(evalSet, draft) {
  const before = {
    name: evalSet?.name || "",
    description: evalSet?.description || "",
    metadata: evalSet?.metadata || {},
    system: evalSet?.judge_prompt?.system_prompt || "",
    user: evalSet?.judge_prompt?.user_prompt || "",
  };
  const changed = [];
  if (before.name !== (draft?.name || "")) changed.push("name");
  if (before.description !== (draft?.description || "")) changed.push("description");
  if (!sameMap(before.metadata, draft?.metadata || {})) changed.push("metadata");
  if (before.system !== (draft?.system || "")) changed.push("judge_system_prompt");
  if (before.user !== (draft?.user || "")) changed.push("judge_user_prompt");
  return changed;
}

function sameMap(a, b) {
  const keys = Object.keys(a);
  if (keys.length !== Object.keys(b).length) return false;
  return keys.every((k) => k in b && String(a[k]) === String(b[k]));
}
