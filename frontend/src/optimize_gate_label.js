// What happened to one step's candidate, in words a reader has not had to learn.
//
// Five places were rendering this verdict and four of them were rendering it
// differently: the chart's tooltip printed `gate_action.replace(/_/g, " ")` —
// "accept new best" — the step table printed `rejected (accuracy)`, the diff
// header printed the raw action again, and the rollout page wrote its own
// sentence. So the same step read as four different outcomes depending on which
// panel you were looking at, and none of the four said what the words meant.
//
// The vocabulary is small, and the reason it is small is worth stating: the
// engine keeps `current_score` and `best_score` in lockstep, so a candidate
// that beats the skill in force has by definition beaten the best (see
// `gating.py`). Plain `accepted` is therefore unreachable today. It is here
// anyway, because the upstream gate can return it and a label that renders as
// `undefined` is a worse way to find that out.
//
// Which number "validation" means is the mode's: an isolated run is gated on the
// judge's verdict on the answers, a routing run on whether the agent opened the
// skills each question is tagged for. The label says which, because "rejected ·
// score" against a chart with two lines on it is a question rather than an
// answer.
//
//   accepted (new best)   validation went up; this is the skill the run hands out
//   accepted              better than the skill in force, but not the run's best
//   rejected · score      isolated: answer accuracy did not beat the best so far
//   rejected · routing    routing: the agent reached the right skills less often
//   rejected · not measurable  routing: nothing in the split could be scored
//   rejected · edits lost the merge stage returned edits naming no file, so none applied
//   rejected · errors     validation never came back, so there was nothing to judge
//   skipped · errors      the training batch never came back, so there was no candidate
//   not judged            still running, or a step that ended before its gate
//
// The two `errors` outcomes are the ones this module exists for. They are not
// the gate refusing a candidate — the gate was never asked — and calling them
// "rejected (errors)" without saying more would leave a reader to conclude
// their edit was bad when what actually happened is that the agent server
// stopped answering.

/** The verdict on one step: `{ tone, label, reason, short, detail }`.
 *
 * `short` is for a badge, `detail` for a tooltip, a readout line or a sentence.
 * `reason` is never shown on its own — it is the half of `short` after the dot,
 * kept separate so a caller with room for one word can take it.
 */
export function gateLabel(step) {
  const state = step || {};
  const action = state.gate_action;

  if (!action) {
    return verdict({
      tone: "neutral",
      label: state.step_no === 0 ? "baseline" : "not judged",
      detail:
        state.step_no === 0
          ? "The skill as it arrived, measured on the validation split. There is no edit to judge yet."
          : "This step has not reached its gate.",
    });
  }

  if (action === "skip") {
    return verdict({
      tone: "warning",
      label: "skipped",
      reason: "system errors",
      detail: `${failureText(state, "train")} There was nothing to reflect on, so this step made no edit and bought no validation rollout.`,
    });
  }

  if (action === "reject") {
    if (state.gate_reject_reason === "val_errors") {
      return verdict({
        tone: "warning",
        label: "rejected",
        reason: "system errors",
        detail: `${failureText(state, "val")} The edit was dropped unjudged rather than accepted on the questions that did answer.`,
      });
    }
    if (state.gate_reject_reason === "routing_unmeasured") {
      return verdict({
        tone: "warning",
        label: "rejected",
        reason: "not measurable",
        detail:
          "Routing accuracy could not be measured on this validation split: no question both produced a trace and carried a skill tag, so there was nothing to score. The edit was dropped rather than judged against a number that does not exist.",
      });
    }
    if (state.gate_reject_reason === "edits_unattributable") {
      return verdict({
        tone: "warning",
        label: "rejected",
        reason: "edits lost",
        detail:
          "Every edit in this step's patch came back naming no file, so none of them could be applied and the candidate was identical to the skills in force. That is the pipeline losing the patch between merge and apply — not a judgement on the edits, which were never tried.",
      });
    }
    // The engine names the measurement that refused it, because the gate
    // compares one number and the page draws two.
    const routing = state.gate_reject_reason === "routing";
    return verdict({
      tone: "neutral",
      label: "rejected",
      reason: routing ? "routing" : "score",
      detail: routing
        ? `Routing accuracy — how often the agent opened the skills a question is tagged for — did not beat the best so far${pct(state.best_score) ? ` (${pct(state.best_score)})` : ""}, so the descriptions in force are unchanged.`
        : `Answer accuracy on validation did not beat the best score so far${pct(state.best_score) ? ` (${pct(state.best_score)})` : ""}, so the skill in force is unchanged.`,
    });
  }

  if (action === "accept_new_best") {
    return verdict({
      tone: "success",
      label: "accepted",
      reason: "new best",
      detail: `Validation beat every earlier step${pct(state.best_score) ? `, at ${pct(state.best_score)}` : ""}. This is the skill the run hands out.`,
    });
  }

  if (action === "accept") {
    return verdict({
      tone: "success",
      label: "accepted",
      detail:
        "Better than the skill in force, though not the best this run has scored. The next step edits this one.",
    });
  }

  // Anything the engine grows later. Readable rather than right.
  return verdict({ tone: "neutral", label: action.replace(/_/g, " ") });
}

/** How much of one split never came back, as a sentence. Exported for the readout. */
export function failureText(step, split) {
  const items = step?.[`${split}_n_items`];
  const scored = step?.[`${split}_n_scored`];
  const name = split === "train" ? "training" : "validation";
  if (!items || scored == null) {
    return `Too much of the ${name} split never came back from the agent.`;
  }
  const failed = items - scored;
  const share = Math.round((failed / items) * 100);
  return `${failed} of ${items} ${name} questions never came back from the agent (${share}%).`;
}

function verdict({ tone, label, reason = null, detail = "" }) {
  return { tone, label, reason, detail, short: reason ? `${label} · ${reason}` : label };
}

function pct(value) {
  return value == null ? "" : `${Math.round(value * 100)}%`;
}
