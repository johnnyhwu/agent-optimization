// Which eval set a question came from, in a table column.
//
// The name alone is ambiguous by design: nothing stops two eval sets being
// called "Billing questions", because a set's identity is its id and the name is
// a label its owner may reuse. On the wizard's skill step that ambiguity lands
// exactly where it hurts — a developer checking which of their two similarly
// named sets a question belongs to, and getting the same word for both.
//
// So the column shows the name with the head of the id under it. The head,
// rather than the whole 36-character UUID, because the column is one of four on
// a dense table and eight hex characters is already far more than enough to tell
// two sets apart by eye; the full value stays in the row's `title` for anyone who
// needs to paste it.

export function shortId(id) {
  const text = String(id ?? "").trim();
  if (!text) return "";
  // The first group of a UUID. A non-UUID id — nothing issues one today, but
  // this column should not become the reason that stays true — is simply cut to
  // the same width.
  const head = text.split("-")[0];
  return head.length > 8 ? head.slice(0, 8) : head;
}

export function evalSetLabel(question) {
  const name = (question?.eval_set_name || "").trim();
  const id = shortId(question?.eval_set_id);
  return { name: name || "(unnamed set)", id, fullId: String(question?.eval_set_id ?? "") };
}
