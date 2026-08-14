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
