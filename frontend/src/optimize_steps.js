// The run overview's step state, as a reducer over the progress stream.
//
// This exists because the overview used to hold the steps like this:
//
//     const steps = live.steps.length ? live.steps : run.steps || [];
//
// where `live.steps` was written by exactly one handler — `snapshot` — and the
// server sends `snapshot` exactly once, when the stream opens. Every later
// event moved a one-line phase caption and nothing else. So for the hour a run
// takes, the chart and the step table showed whatever was true at the moment
// the page loaded. Worse at the end: `run_completed` refetched the run, but the
// stale snapshot was still non-empty and therefore still won, so the finished
// numbers never appeared without a hard reload.
//
// The fix is not more handlers on the old shape — it is admitting that a step
// row is assembled from several events that arrive minutes apart. A step
// publishes `step_started`, then `rollout_done` twice (train, then val, against
// two different skills), then `update_done`, then `gate_done`. Each carries a
// slice of the row. So: a map keyed by `step_no`, events merge fields into it,
// and a refetch replaces it outright.
//
// Two rules make the merge safe, and both are tested below:
//
//   1. Events only ever *add* what they carry. An event that arrives late, or
//      twice, re-writes the same fields with the same values; it can never
//      blank a field some other event already filled.
//   2. A refetch is authoritative and replaces the whole map. It is the only
//      thing that can remove a step, and it is what runs on `resync` (the hub
//      dropped events), on `run_completed`, and on the Refresh button.
//
// Pure, so `node --test` can load it — which is the point, since none of this
// is observable in the component until an hour-long run is halfway through.

// Field names on the summary row are prefixed by split, because train and val
// measure two different skills within the same step and must never be averaged
// into one "hard". `rollout_done` carries the split and the bare names.
const ROLLOUT_FIELDS = [
  "hard",
  "soft",
  "activation_rate",
  "n_items",
  "n_scored",
  "n_agent_error",
  "n_judge_error",
  "latency_min_ms",
  "latency_p50_ms",
  "latency_mean_ms",
  "latency_max_ms",
];

// The stages of one step, in the order the engine performs them. The header
// draws this as a strip with the current one lit, because "step 3 · rollout"
// told a reader what was happening without telling them where that sits in the
// step, how many stages are left, or how long any of it should take.
//
// The baseline performs only the first of them — it is one validation rollout
// and nothing else — which is why the strip is driven by the phase rather than
// by a fixed list of five lamps that would sit dark for the whole of step 0.
export const STEP_PHASES = [
  { key: "rollout_train", label: "Answer", hint: "the training questions, with the skill as it stands" },
  { key: "reflect", label: "Reflect", hint: "the analyst reads the failures" },
  { key: "update", label: "Edit", hint: "the proposed edits are applied to the skill" },
  { key: "rollout_val", label: "Validate", hint: "the validation questions, with the edited skill" },
  { key: "gate", label: "Gate", hint: "keep the edit, or roll it back" },
];

export function emptySteps() {
  return { byNo: new Map(), activity: null };
}

// The array the chart and the table read. Sorted by step number rather than by
// arrival: `rollout_done` for step 4 can land before `gate_done` for step 3,
// and a chart whose x-axis followed insertion order would draw a zigzag.
export function stepList(state) {
  return [...state.byNo.values()].sort((a, b) => a.step_no - b.step_no);
}

// The server's version of the truth, from `snapshot` or from a refetch of the
// run. Replaces rather than merges — see rule 2 above.
export function replaceSteps(state, steps) {
  const byNo = new Map();
  for (const step of steps || []) byNo.set(step.step_no, step);
  return { ...state, byNo };
}

// `run_completed` is the one event that says nothing about a step. It clears
// what the run is doing; the caller refetches for the final rows.
export function applyEvent(state, type, data) {
  if (!data || typeof data.step_no !== "number") {
    return type === "run_completed" ? { ...state, activity: null } : state;
  }

  const activity = activityFor(type, data, state.activity) ?? state.activity;
  const patch = patchFor(type, data);
  if (!patch) return { ...state, activity };

  const byNo = new Map(state.byNo);
  const before = byNo.get(data.step_no) || blankStep(data);
  byNo.set(data.step_no, { ...before, ...patch });
  return { byNo, activity };
}

// A step the stream told us about before any refetch did. `status: "running"`
// rather than the server's eventual "done": every numeric field is absent, and
// a row claiming to be finished with no numbers on it is how the table would
// render an em-dash where a measurement is still coming.
function blankStep(data) {
  return {
    step_no: data.step_no,
    epoch_no: data.epoch_no ?? null,
    step_in_epoch: data.step_in_epoch ?? null,
    status: "running",
  };
}

function patchFor(type, data) {
  if (type === "step_started") {
    // Only the identity fields. Deliberately not `status`, so a `step_started`
    // replayed after the step finished cannot walk a done row back to running.
    const patch = {};
    if (data.epoch_no != null) patch.epoch_no = data.epoch_no;
    if (data.step_in_epoch != null) patch.step_in_epoch = data.step_in_epoch;
    return patch;
  }

  if (type === "rollout_done") {
    const split = data.split === "train" ? "train" : "val";
    const patch = {};
    for (const field of ROLLOUT_FIELDS) {
      if (data[field] !== undefined) patch[`${split}_${field}`] = data[field];
    }
    // The baseline finishes here, and only here. `_run_baseline` in the engine
    // is one validation rollout and nothing else — no training rollout, no
    // reflect, no update, and no gate — so step 0 never publishes the
    // `gate_done` that ends every other step, even though the server does write
    // it to the database as `done`. Without this the chart's first point would
    // sit at "running" for the whole run, until some unrelated refetch settled
    // it. The honest fix is a `step_done` event from the engine; until there is
    // one, this is the engine's own definition of the baseline, not a guess.
    if (data.step_no === 0 && split === "val") patch.status = "done";
    return patch;
  }

  if (type === "update_done") {
    return {
      n_edits_applied: data.n_edits_applied,
      lines_added: data.lines_added,
      lines_removed: data.lines_removed,
    };
  }

  if (type === "gate_done") {
    return {
      status: "done",
      gate_action: data.action,
      gate_reject_reason: data.reject_reason ?? null,
      candidate_from_cache: Boolean(data.from_cache),
      current_score: data.current_score ?? null,
      best_score: data.best_score ?? null,
    };
  }

  return null;
}

// What the run is doing right now, as structure rather than as a sentence.
//
// This used to return a string — "step 3 · rollout" — which is all the header
// had to show for the several minutes a rollout takes. It said which step and
// roughly what, and nothing about how far through: no count of questions
// answered, no sense of which stage of the step this is, no way to tell a
// rollout that is nearly finished from one that has just begun.
//
// Returning an object lets the header draw a stage strip and a per-question
// count, and keeps the phrasing in the component where phrasing belongs. The
// shape is:
//
//   { stepNo, phase, done, total, note }
//
// `done`/`total` are present only during a rollout, which is the only stage
// with countable work in it. `note` is a short sentence for the stages that
// have something worth saying and nothing to count.
//
// Events *after* a stage report its completion, so each one names the stage the
// engine has moved on to — `reflect_done` means the analyst has answered and the
// edits are next. `gate_done` is the exception: it ends the step, so it reports
// the verdict rather than a following stage.
function activityFor(type, data, previous) {
  const at = (phase, extra = {}) => ({ stepNo: data.step_no, phase, ...extra });

  switch (type) {
    case "step_started":
      // The baseline answers the validation split; every other step starts on
      // training. `_run_baseline` publishes `phase: "baseline"`.
      return at(data.phase === "baseline" ? "rollout_val" : "rollout_train");
    case "rollout_progress":
      return at(data.split === "train" ? "rollout_train" : "rollout_val", {
        done: data.done,
        total: data.total,
      });
    case "rollout_done":
      // Train finishing hands over to reflection; validation finishing hands
      // over to the gate. Which is also why the strip is ordered the way it is.
      return data.split === "train"
        ? at("reflect")
        : at("gate");
    case "reflect_done":
      return at("update", {
        note: `${data.n_minibatches ?? "?"} minibatch${data.n_minibatches === 1 ? "" : "es"} reflected on`,
      });
    case "update_done":
      return at("rollout_val", {
        note:
          data.n_edits_applied != null
            ? `${data.n_edits_applied} edit${data.n_edits_applied === 1 ? "" : "s"} applied`
            : null,
      });
    case "gate_done":
      return at("gate", {
        note: data.action === "reject" ? "candidate rejected" : "candidate kept",
      });
    case "slow_update_done":
      // Between epochs, not inside a step. Keeps whichever stage was showing
      // rather than blanking the strip for a pass that belongs to neither.
      return { ...(previous || {}), note: `epoch ${data.epoch_no}: guidance written` };
    default:
      return null;
  }
}

// How far along the run is, counting the baseline as a step — it costs a full
// validation rollout and it is the line every later step is compared against.
//
// The rail and the panel disagreed on this: the rail rendered `steps_done /
// total_steps` and the panel `steps.length / total_steps + 1`, so the same run
// read as 4/12 in one and 5/13 in the other, side by side on one screen. One
// function now, and it declines to render a denominator it does not have rather
// than printing `NaN`.
// Both sides count *finished* steps, which is what the server's `steps_done`
// means (`COUNT(*) WHERE status = 'done'`, baseline included). Counting rows
// instead would make the panel jump a step ahead of the rail the moment one
// started, which is the same disagreement in a subtler form.
export function stepProgress(run, steps) {
  const done = steps
    ? steps.filter((s) => s.status === "done").length
    : run?.steps_done ?? 0;
  const total = run?.total_steps == null ? null : run.total_steps + 1;
  return { done, total, label: total == null ? `${done}` : `${done}/${total}` };
}
