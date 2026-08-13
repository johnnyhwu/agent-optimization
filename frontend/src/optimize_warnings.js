// The run overview's standing warnings.
//
// Every rule here describes a run that *looks* successful. The chart climbs,
// the gate accepts, a skill is downloadable — and the number the chart is
// drawing does not mean what a reader will take it to mean. That is the whole
// reason these sit on the overview instead of inside a detail page: by the time
// anyone opens a detail page they are already suspicious, and these are for the
// case where nobody is.
//
// A pure function over the payload the overview already has, so it is testable
// and so the same run always produces the same list. It reads only what the
// server measured — in particular `n_answer_leaks`, counted when each candidate
// was written, because searching for them here would mean a diff per step
// inside a page that reloads while the run is still going.

// Below this share of validation questions, "the agent read the skill" stops
// being a safe assumption about the numbers beside it.
export const ACTIVATION_FLOOR = 0.8;
// Nothing in the loop pushes back on length. This is where "it grew" turns into
// "it grew enough that somebody should look".
export const GROWTH_FACTOR = 2;

export function runWarnings(run, steps) {
  const out = [];
  if (!run) return out;
  const list = steps || [];

  if (run.overlap_item_keys?.length) {
    out.push({
      id: "overlap",
      tone: "warning",
      title: "Validation is not fully held out",
      body: `${run.overlap_item_keys.length} question(s) are in both splits, so part of what the gate measured is the skill being fitted to them.`,
    });
  }

  const preflight = run.detector?.preflight;
  if (preflight && !preflight.ok) {
    out.push({
      id: "detector",
      tone: "warning",
      title: "The skill could not be seen in the trace",
      body: preflight.message || "Neither detector found the skill being read.",
    });
  }

  // The best step, because that is the skill the download button hands out.
  // Falling back to the last measured step keeps a run that has not chosen a
  // best yet — one still running, or cancelled early — from silently skipping
  // every check below.
  const best =
    list.find((s) => s.step_no === run.best_step) ||
    [...list].reverse().find((s) => s.val_activation_rate != null || s.skill_len != null);

  if (best) {
    if (best.val_activation_rate == null) {
      // Not the same as "no". The detectors disagreeing or finding nothing to
      // go on is a fact about the measurement, and saying "0%" about it would
      // accuse the run of something nobody observed.
      out.push({
        id: "activation-unknown",
        tone: "info",
        title: "Whether the agent read the skill is unknown",
        body: "Neither detector could tell for step " + best.step_no +
          ", so the accuracy beside it cannot be attributed to the skill with confidence.",
      });
    } else if (best.val_activation_rate < ACTIVATION_FLOOR) {
      out.push({
        id: "activation-low",
        tone: "warning",
        title: "The agent was rarely seen reading this skill",
        body: `On step ${best.step_no} the skill was read on ${pct(best.val_activation_rate)} of validation questions. Accuracy that moves while the skill is unread moved for some other reason.`,
      });
    }

    const baseline = list.find((s) => s.step_no === 0);
    if (baseline?.skill_len && best.skill_len && best.step_no !== 0) {
      const ratio = best.skill_len / baseline.skill_len;
      if (ratio >= GROWTH_FACTOR) {
        out.push({
          id: "skill-size",
          tone: "warning",
          title: "This skill is much larger than the one it started from",
          body: `Step ${best.step_no} is ${ratio.toFixed(1)}× the size of the original. The gate only asks whether accuracy went up, so nothing in the loop pushes back on length — and every line is paid for on every call the agent makes.`,
        });
      }
    }
  }

  // A run is a comparison, and it only holds if the other side held still. Each
  // step records the agent config it actually ran against; a step that saw a
  // different one than the run pinned was measuring a different system, and the
  // only symptom otherwise is the accuracy moving — which is what the chart is
  // for. `null` means the probe never happened, which is not a mismatch.
  if (run.workspace_version) {
    const drifted = list.filter(
      (s) => s.workspace_version && s.workspace_version !== run.workspace_version,
    );
    if (drifted.length) {
      out.push({
        id: "workspace-drift",
        tone: "warning",
        title: "The agent changed while this run was going",
        body: `${label(drifted)} ran against a different agent config than the ${run.workspace_version} this run started from. Steps either side of that are measurements of two different systems, and the gate compared them as though they were one.`,
      });
    }
  }

  // Split by whether the gate kept the step, because the two are different
  // problems: one is a bad artefact, the other is a near miss.
  const leaked = list.filter((s) => s.n_answer_leaks > 0);
  const kept = leaked.filter((s) => s.gate_action && s.gate_action !== "reject");
  const dropped = leaked.filter((s) => !s.gate_action || s.gate_action === "reject");

  if (kept.length) {
    out.push({
      id: "answer-leak",
      tone: "error",
      title: "This skill may contain memorised answers",
      body: `${plural(kept, "step")} the gate accepted (${label(kept)}) added a training question's gold answer word for word. That raises training accuracy without teaching the agent anything, and anyone who deploys this skill deploys a lookup table for the questions this eval set happened to hold.`,
    });
  }
  if (dropped.length) {
    out.push({
      id: "answer-leak-rejected",
      tone: "warning",
      title: "A rejected step tried to memorise an answer",
      body: `${label(dropped)} added a gold answer verbatim, and the gate turned those edits down. The text is not in the skill — but the gate rejected them on accuracy, not because it noticed, so this happened to be caught rather than being prevented.`,
    });
  }

  return out;
}

function label(steps) {
  const numbers = steps.map((s) => s.step_no);
  return `${numbers.length > 1 ? "steps" : "step"} ${numbers.join(", ")}`;
}

function plural(steps, word) {
  return steps.length > 1 ? `${steps.length} ${word}s` : `A ${word}`;
}

function pct(value) {
  return `${Math.round(value * 100)}%`;
}
