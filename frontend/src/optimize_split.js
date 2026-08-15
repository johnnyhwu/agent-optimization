// The train/validation split, as data the wizard edits.
//
// Kept out of the component for the reason every other pure module here is:
// `node --test` can reach it, and this repo has no component-test harness. That
// constraint is worth respecting rather than working around — what actually
// goes wrong in a split editor is the rules, not the markup.
//
// Three of those rules are worth stating up front, because each one looks like
// a detail and is not:
//
//   * **Move removes; duplicate does not.** A "move" that only added would put
//     the question in both columns — the overlap case — without the warning
//     that makes overlap a decision instead of an accident.
//   * **Exclude is reversible and is not deletion.** It means "not in this
//     run". The eval set is untouched, which is why the control is an ✕ rather
//     than a bin and why excluded questions stay listed.
//   * **The size thresholds come from the server.** A copy here would drift
//     from the one the create endpoint enforces, and Start would be enabled on
//     a request that 400s.

export const DEFAULT_SORT = "question_id";

// One split, as the editor holds it: the full question list plus three key
// lists. Keys rather than objects so a question in both columns is one object
// and two memberships — which is exactly what it is.
export function makeSplit(questions, { train = [], val = [], excluded = [] } = {}) {
  const known = new Set(questions.map((q) => q.item_key));
  const keep = (keys) => keys.filter((k) => known.has(k));
  return {
    questions,
    byKey: new Map(questions.map((q) => [q.item_key, q])),
    train: keep(train),
    val: keep(val),
    excluded: keep(excluded),
  };
}

const other = (column) => (column === "train" ? "val" : "train");

function withLists(split, lists) {
  // A new object every time: React re-renders on identity, and an in-place
  // mutation would update the data while leaving the screen showing the
  // previous split — which the developer would then start.
  return { ...split, ...lists };
}

export function move(split, key, to) {
  if (!split.byKey.has(key)) return split;
  const from = other(to);
  return withLists(split, {
    [from]: split[from].filter((k) => k !== key),
    [to]: split[to].includes(key) ? split[to] : [...split[to], key],
    excluded: split.excluded.filter((k) => k !== key),
  });
}

export function duplicate(split, key, to) {
  if (!split.byKey.has(key)) return split;
  if (split[to].includes(key)) return split;
  return withLists(split, {
    [to]: [...split[to], key],
    excluded: split.excluded.filter((k) => k !== key),
  });
}

export function exclude(split, key) {
  if (!split.byKey.has(key)) return split;
  return withLists(split, {
    train: split.train.filter((k) => k !== key),
    val: split.val.filter((k) => k !== key),
    excluded: split.excluded.includes(key) ? split.excluded : [...split.excluded, key],
  });
}

export function restore(split, key) {
  if (!split.byKey.has(key)) return split;
  return withLists(split, {
    excluded: split.excluded.filter((k) => k !== key),
    train: split.train.includes(key) ? split.train : [...split.train, key],
  });
}

// What the three icon buttons on a row may do, and why not when they may not.
// A disabled control with no explanation is a puzzle, so every refusal carries
// the sentence its tooltip shows.
export function actionsFor(split, key, column) {
  const target = other(column);
  const inBoth = split.train.includes(key) && split.val.includes(key);
  const alreadyThere = split[target].includes(key);
  const reason = alreadyThere
    ? `this question is already in ${target === "val" ? "validation" : "training"}`
    : null;
  return {
    inBoth,
    move: {
      enabled: !alreadyThere,
      reason,
      label: target === "val" ? "Move to validation" : "Move to training",
    },
    duplicate: {
      enabled: !alreadyThere,
      reason,
      label:
        target === "val"
          ? "Also add to validation (keep here)"
          : "Also add to training (keep here)",
    },
    exclude: { enabled: true, reason: null, label: "Exclude from this run" },
  };
}

export function sortQuestions(questions, mode = DEFAULT_SORT) {
  const list = [...questions];
  if (mode === "accuracy") {
    // Failing questions first — they are the ones a skill edit might fix, and
    // the ones worth putting in the training split. Never-run questions sort
    // last rather than as 0%: presenting "no data" as "always wrong" would put
    // them at the top as the worst in the set.
    return list.sort((a, b) => {
      const au = a.prior_accuracy == null;
      const bu = b.prior_accuracy == null;
      if (au !== bu) return au ? 1 : -1;
      if (au) return 0;
      return a.prior_accuracy - b.prior_accuracy;
    });
  }
  if (mode === "eval_set") {
    return list.sort(
      (a, b) =>
        a.eval_set_name.localeCompare(b.eval_set_name) ||
        a.question_id.localeCompare(b.question_id),
    );
  }
  return list.sort((a, b) => a.question_id.localeCompare(b.question_id));
}

export function counts(split) {
  const val = new Set(split.val);
  return {
    train: split.train.length,
    val: split.val.length,
    overlap: split.train.filter((k) => val.has(k)).length,
    excluded: split.excluded.length,
    total: split.questions.length,
  };
}

// The same rules as `app/optimizer/dataset.split_issues`, evaluated against the
// limits the server sent. Checking here as well is not duplication for its own
// sake: it is what lets the developer find out while they can still fix it,
// instead of on a 400 three screens later.
//
// Each issue is four pieces rather than one sentence, because one sentence had
// to be three things at once and managed none of them. `title` is what is wrong,
// in the fewest words that can be scanned past; `summary` is the number that
// makes it true; `detail` is why the number matters, which nobody needs on the
// way past and everybody needs once; and `suggestion` is the move — the part
// that was missing entirely. "14 training questions is workable but thin"
// described the split accurately and left the developer with nothing to do
// about it.
//
// The screen shows title + summary, and folds the other two away behind a
// disclosure. `message` is kept as title + summary joined, because it is what a
// caller with one line to spend still wants.
function issue({ level, code, title, summary, detail, suggestion, ...rest }) {
  return {
    level, code, title, summary, detail, suggestion,
    message: `${title} — ${summary}`,
    ...rest,
  };
}

export function splitIssues(split, limits = {}) {
  const {
    min_train: minTrain = 8,
    min_val: minVal = 5,
    warn_train: warnTrain = 20,
    warn_val: warnVal = 10,
  } = limits;
  const { train, val, overlap } = counts(split);
  const issues = [];

  if (train < minTrain) {
    issues.push(issue({
      level: "error",
      code: "train_too_small",
      title: "Too few training questions to run",
      summary: `${train} in the training column; ${minTrain} is the minimum.`,
      detail:
        "Each step reflects on one minibatch at a time and looks for what the "
        + "failures have in common. Below this many questions a minibatch is a "
        + "handful of unrelated cases, and the edit it produces is fitted to "
        + "whichever one happened to be in it.",
      suggestion:
        `Move ${minTrain - train} more question(s) into Training, or go back a `
        + "step and pick an eval set with more questions tagged for this skill.",
    }));
  } else if (train < warnTrain) {
    issues.push(issue({
      level: "warning",
      code: "train_small",
      title: "The training split is thin",
      summary: `${train} questions — ${warnTrain} or more is where reflection starts generalising.`,
      detail:
        "The optimizer writes an edit from what repeats across a batch. With a "
        + "small split there is little repetition to find, so the edits tend to "
        + "name specific questions instead of the pattern behind them — which "
        + "improves those questions and nothing else.",
      suggestion:
        "The run is still worth doing; read the step diffs and be suspicious of "
        + "edits that mention one question's subject matter. To widen it, add "
        + `${warnTrain - train} more questions from another eval set.`,
    }));
  }

  if (val < minVal) {
    issues.push(issue({
      level: "error",
      code: "val_too_small",
      title: "Too few validation questions to run",
      summary: `${val} in the validation column; ${minVal} is the minimum.`,
      detail:
        "Validation is what decides whether an edit is kept. With this few "
        + "questions the comparison is not a measurement — one question "
        + "answering differently would swing the verdict on its own.",
      suggestion:
        `Move ${minVal - val} question(s) from Training into Validation. Taking `
        + "them from Training is better than duplicating them: a question in "
        + "both columns is not held out.",
    }));
  } else if (val < warnVal) {
    issues.push(issue({
      level: "warning",
      code: "val_small",
      title: "The gate will be noisy",
      summary: `${val} validation questions, so one answer moves accuracy by ${Math.round(100 / val)} points.`,
      detail:
        "After each step the candidate skill is kept only if it scores better "
        + "on this column. When one question is worth "
        + `${Math.round(100 / val)} points, an agent that answers differently `
        + "for reasons of its own can both keep a bad edit and reject a good "
        + "one, and the chart will look like progress either way.",
      suggestion:
        `Move questions into Validation until it holds at least ${warnVal} — `
        + "Training can usually spare them more cheaply than the gate can.",
    }));
  }

  if (overlap) {
    const valSet = new Set(split.val);
    issues.push(issue({
      level: "warning",
      code: "overlap",
      item_keys: split.train.filter((k) => valSet.has(k)),
      title: "Validation is not fully held out",
      summary: `${overlap} question(s) are in both columns.`,
      detail:
        "Those questions are learned from and then used to judge the learning. "
        + "The gate will see improvement on them because the edit was written "
        + "against them, so part of every score after this is the skill being "
        + "fitted to questions it has already read.",
      suggestion:
        "Intentional for a question that defines what the skill is for. "
        + "Otherwise use the ✕ on one of the two copies, or the ← / → button to "
        + "move it rather than share it.",
    }));
  }
  return issues;
}

export function canStart(split, limits) {
  return !splitIssues(split, limits).some((i) => i.level === "error");
}
