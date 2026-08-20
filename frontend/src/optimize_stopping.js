// What will end this run, and — once it has ended — what did.
//
// A run used to have exactly one ending a reader could see: the step counter
// reaching the number the wizard quoted. Everything else was invisible. It
// could also stop because a rollout failed twice, and that rule lived in one
// branch of the engine, was configurable only through the API, and announced
// itself as a failed run with a sentence about a batch.
//
// Now there are four conditions and they are settings, so the page has to say
// what they are while the run is going and which one fired when it stops. Both
// halves are here rather than in the header component, because both are rules —
// "how close is this run to running out of patience" is arithmetic over the
// steps, and arithmetic in a component is arithmetic nobody can test.
//
// The streak arithmetic below mirrors `store._trailing_streak` on the server.
// That is a deliberate second implementation, in the same spirit as the diff
// counts in `skillio.py`/`diff.js`: the server's copy decides whether the run
// stops, this one only says how close it is, and the rule is four lines. What
// they must agree on is which steps a split's streak may *skip* — a step whose
// training batch never came back never reached its validation rollout, so it
// says nothing either way about whether validation is answering.

import { plural } from "./plural.js";

/** The conditions that can end this run, with how close each one is.
 *
 * `{ id, label, progress, met }` per condition, in the order they are worth
 * reading. A condition that is switched off is absent rather than present and
 * greyed: the row is a list of what can happen, and "0 — never" is not one of
 * the things that can happen.
 */
export function stopConditions(run, steps) {
  if (!run) return [];
  const config = run.config || {};
  const list = steps || [];
  const done = list.filter((s) => s.status === "done").length;
  const out = [];

  out.push({
    id: "steps",
    label: "every step run",
    progress: run.total_steps ? `${Math.min(done, run.total_steps + 1)}/${run.total_steps + 1}` : `${done}`,
    met: run.total_steps != null && done > run.total_steps,
  });

  const patience = number(config.early_stop_patience);
  if (patience > 0) {
    const since = stepsSinceBest(run, list);
    out.push({
      id: "patience",
      label: "no new best",
      progress: `${Math.min(since, patience)}/${patience} steps`,
      met: since >= patience,
    });
  }

  // `> 0`, like every other number here: zero means off. A target of 0% would
  // otherwise be listed as a live condition that every run has already met.
  const target = number(config.early_stop_target_score, null);
  if (target != null && target > 0) {
    out.push({
      id: "target",
      label: "validation target",
      progress: `${pct(run.best_score)} of ${pct(target)}`,
      met: run.best_score != null && run.best_score >= target,
    });
  }

  for (const split of ["val", "train"]) {
    const limit = number(config[`early_stop_${split}_error_streak`]);
    if (limit <= 0) continue;
    const share = number(config[`early_stop_${split}_error_share`]);
    const streak = errorStreak(list, split);
    out.push({
      id: `${split}-errors`,
      label: `${split === "val" ? "validation" : "training"} questions unanswered`,
      progress: `${Math.min(streak, limit)}/${limit} steps over ${pct(share)}`,
      met: streak >= limit,
    });
  }

  return out;
}

/** Why the run ended, or null when its ending needs no explaining.
 *
 * `finished`, `cancelled` and `failed` all return null: the header already says
 * those, and a second sentence repeating the badge is noise. Null is also what
 * a run from before `stop_reason` existed returns, which is the same answer for
 * a different reason — nobody recorded why it stopped.
 */
export function stopSentence(run, steps) {
  const reason = run?.stop_reason;
  if (!reason || reason === "finished" || reason === "cancelled" || reason === "failed") {
    return null;
  }
  const config = run.config || {};
  const at = lastStepNo(steps);
  const where = at == null ? "" : ` at step ${at}`;

  if (reason === "early_stop_patience") {
    const patience = number(config.early_stop_patience);
    return `Stopped early${where}: ${plural(patience, "step")} in a row failed to beat the best score.`;
  }
  if (reason === "early_stop_target") {
    return `Stopped early${where}: validation reached the target of ${pct(config.early_stop_target_score)}.`;
  }
  if (reason === "early_stop_val_errors" || reason === "early_stop_train_errors") {
    const split = reason === "early_stop_val_errors" ? "val" : "train";
    const limit = number(config[`early_stop_${split}_error_streak`]);
    const name = split === "val" ? "validation" : "training";
    return (
      `Stopped early${where}: ${plural(limit, `${name} rollout`)} in a row came back with ` +
      `more than ${pct(config[`early_stop_${split}_error_share`])} of their questions unanswered. ` +
      "That is the agent server, not the skill — the finished steps are still on disk."
    );
  }
  return `Stopped early${where}: ${reason.replace(/_/g, " ")}.`;
}

/** How many steps have finished since the one holding the best score. */
export function stepsSinceBest(run, steps) {
  const last = lastStepNo(steps);
  if (last == null || run?.best_step == null) return 0;
  return Math.max(0, last - run.best_step);
}

/** How many of the last steps in a row had *split*'s questions go unanswered. */
export function errorStreak(steps, split) {
  const reason = `${split}_errors`;
  const other = split === "val" ? "train_errors" : "val_errors";
  let streak = 0;
  for (const step of [...(steps || [])].reverse()) {
    if (step.status !== "done") continue;
    if (step.gate_reject_reason === reason) streak += 1;
    // A step that never reached this split neither adds to the streak nor
    // clears it — see the note at the top.
    else if (split === "val" && step.gate_reject_reason === other) continue;
    else if (split === "train" && step.step_no === 0) continue;
    else break;
  }
  return streak;
}

function lastStepNo(steps) {
  const done = (steps || []).filter((s) => s.status === "done");
  return done.length ? done[done.length - 1].step_no : null;
}

function number(value, fallback = 0) {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function pct(value) {
  return value == null ? "—" : `${Math.round(value * 100)}%`;
}
