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
    issues.push({
      level: "error",
      code: "train_too_small",
      message: `${train} training questions — at least ${minTrain} are needed for a minibatch to say anything about a pattern.`,
    });
  } else if (train < warnTrain) {
    issues.push({
      level: "warning",
      code: "train_small",
      message: `${train} training questions is workable but thin — reflection generalises from what repeats across a batch.`,
    });
  }

  if (val < minVal) {
    issues.push({
      level: "error",
      code: "val_too_small",
      message: `${val} validation questions — at least ${minVal} are needed before an accuracy comparison means anything.`,
    });
  } else if (val < warnVal) {
    issues.push({
      level: "warning",
      code: "val_small",
      message: `With ${val} validation questions each one moves accuracy by ${Math.round(100 / val)} points, so the gate will be noisy.`,
    });
  }

  if (overlap) {
    const valSet = new Set(split.val);
    issues.push({
      level: "warning",
      code: "overlap",
      item_keys: split.train.filter((k) => valSet.has(k)),
      message: `${overlap} question(s) are in both splits. Validation is not held out for those, so the gate will read improvements that are partly the skill being fitted to them.`,
    });
  }
  return issues;
}

export function canStart(split, limits) {
  return !splitIssues(split, limits).some((i) => i.level === "error");
}
