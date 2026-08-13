// Part 1's arithmetic: how a rollout's questions are grouped, and what the
// truncation ledger adds up to.
//
// The grouping is the page's whole claim. "These failures were shown to one
// analyst together, and the patch below came from seeing them side by side" is
// only true if the grouping comes from `minibatch_no`, which the reflect stage
// writes back onto each result. Any other arrangement — by verdict, by
// arrival, by anything the browser could invent — looks exactly as convincing
// and is a fiction.
//
// It lives here rather than in the component because this is the half that can
// be tested: `node --test` loads pure modules only.

/** What happened to one question: the vocabulary the whole page is coloured by.
 *
 * `error` is deliberately not a kind of wrong. `score_rollout` leaves
 * infrastructure failures out of both the numerator and the denominator, so a
 * page that painted them like wrong answers would contradict the accuracy
 * printed above them.
 */
export function outcomeOf(result) {
  if (result.status === "failed" || result.status === "cancelled") return "error";
  if (result.status === "pending" || result.verdict == null) return "pending";
  if (result.verdict === "correct") return "correct";
  // Partial credit is what separates the soft metric from the hard one. Folded
  // into "incorrect", the gap between the two figures becomes unexplainable
  // from the page that exists to explain it.
  return result.judge_score > 0 ? "partial" : "incorrect";
}

function emptyCounts() {
  return { correct: 0, partial: 0, incorrect: 0, error: 0, pending: 0 };
}

function countOf(results) {
  const counts = emptyCounts();
  for (const result of results) counts[outcomeOf(result)] += 1;
  return counts;
}

/** The list as the page renders it: one section per analyst call.
 *
 * Validation gets a single flat section — it is measured and never reflected
 * on, and a minibatch heading over held-out questions would imply the edits
 * were derived from them.
 *
 * A training question that fed no analyst (a correct answer, when the run is
 * failure-only) still gets a section of its own at the end. It was rolled out,
 * it was paid for, and it is part of the denominator of the accuracy in the
 * header — a list that omitted it would not add up to its own summary.
 */
export function groupResults(detail) {
  const results = detail.results || [];
  const minibatches = detail.split === "train" ? detail.minibatches || [] : [];

  if (!minibatches.length) {
    return [{ minibatch_no: null, minibatch: null, results, counts: countOf(results) }];
  }

  const byNumber = new Map();
  const ungrouped = [];
  for (const result of results) {
    if (result.minibatch_no == null) ungrouped.push(result);
    else {
      if (!byNumber.has(result.minibatch_no)) byNumber.set(result.minibatch_no, []);
      byNumber.get(result.minibatch_no).push(result);
    }
  }

  // Driven by the minibatch list, not by the results: a batch whose questions
  // all failed has no rows left, and it is still an analyst call that was made.
  const groups = [...minibatches]
    .sort((a, b) => a.minibatch_no - b.minibatch_no)
    .map((minibatch) => {
      const rows = byNumber.get(minibatch.minibatch_no) || [];
      return {
        minibatch_no: minibatch.minibatch_no,
        minibatch,
        results: rows,
        counts: countOf(rows),
      };
    });

  if (ungrouped.length) {
    groups.push({
      minibatch_no: null,
      minibatch: null,
      results: ungrouped,
      counts: countOf(ungrouped),
    });
  }
  return groups;
}

/** What the cascade cut from one analyst call, as a line a person can read.
 *
 * Counted by question rather than by ledger entry: one trace can lose several
 * spans, and reporting "3 traces truncated" when one trace was cut three times
 * overstates how much of the batch was damaged — which is exactly the
 * judgement the line exists to support.
 */
export function truncationSummary(minibatch) {
  const entries = minibatch.truncation || [];
  const items = new Set(entries.map((e) => e.item_key));
  // The cascade's last stage drops whole questions rather than cutting further.
  // "Never shown to the analyst" is a different fact from "shown in shortened
  // form", and only the first changes what the patch could rest on at all.
  const dropped = [...new Set(
    entries.filter((e) => e.stage === "dropped_item").map((e) => e.item_key),
  )];
  return {
    entries: entries.length,
    itemsTruncated: items.size,
    dropped,
    truncated: items.size > 0,
    before: minibatch.chars_before ?? null,
    after: minibatch.chars_after ?? null,
    nItems: minibatch.n_items ?? 0,
  };
}

/** How many edits this analyst proposed. Zero when the call failed. */
export function editsProposed(minibatch) {
  return minibatch.raw_output?.patch?.edits?.length ?? 0;
}
