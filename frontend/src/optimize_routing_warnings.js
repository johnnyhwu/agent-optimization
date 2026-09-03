// Routing configurations the wizard used to accept and the run could not survive.
//
// `optimize_warnings.js` is the same idea one stage later: it describes runs
// that *look* successful and are not. These describe runs that cannot succeed
// at all, caught while the settings are still on screen — because the symptom
// of every one of them is a run that completes normally with a flat chart, and
// working backwards from a flat chart to "your edit budget was smaller than
// your skill count" is an afternoon.
//
// All of them are routing's. Isolated sends one skill to the agent, edits its
// body and is graded by the judge, so none of the arithmetic below applies to
// it — and each function checks the mode itself rather than trusting the caller,
// because these are rendered from two different steps of the wizard.
//
// Pure, and tested, for the reason `frontend/CLAUDE.md` gives: `node --test`
// can load a module and cannot load JSX, so a rule left inside a component is a
// rule that will never be checked.

// Below this many questions per skill in a step, one failure is a large share
// of what that skill's description is being rewritten from. Three sentences
// re-derived from two questions is the variance that makes a routing run
// oscillate rather than converge.
export const MIN_QUESTIONS_PER_SKILL = 5;

// What the batch would want if the split could supply it. Enough for a class of
// question to be visible as a class rather than as a coincidence.
export const SUGGESTED_QUESTIONS_PER_SKILL = 8;

// Under this many validation questions, a strict set match moves in steps too
// large for the gate to find a direction in.
export const SMALL_VAL_SPLIT = 30;

// At or under this many, one untagged validation question stops being noise in
// the baseline and starts being most of it — and all of them being untagged
// aborts the run outright. See the warning that uses it.
export const TINY_VAL_SPLIT = 4;

/** The batch size a routing run wants: 8 per skill, or the whole split. */
export function suggestedBatchSize(trainSize, skills) {
  const n = (skills || []).length;
  if (!n || !trainSize) return 0;
  return Math.min(trainSize, n * SUGGESTED_QUESTIONS_PER_SKILL);
}

function questionsOf(preview, skills) {
  // By key, because routing files a question under every skill it names — the
  // groups deliberately sum to more than the questions imported.
  const seen = new Map();
  for (const group of preview?.groups || []) {
    if (!skills.includes(group.skill_name)) continue;
    for (const question of group.questions || []) {
      seen.set(question.item_key, question);
    }
  }
  return [...seen.values()];
}

/**
 * What is wrong with the *selection*, for the step where it can be fixed.
 *
 * Both rules are about a skill that is in the run's arithmetic without being in
 * the run's control, which is the shape of routing mistake that produces a
 * confident, wrong number rather than an error.
 */
export function routingSkillWarnings({ mode, skills = [], preview }) {
  const out = [];
  if (mode !== "routing" || !skills.length) return out;

  // A question's tags are pinned unfiltered when the run starts, and routing
  // scores an exact set match against them. So a question tagged for a skill
  // that is not under optimisation has exactly one correct outcome — the agent
  // opens a description nothing in this run may edit — and it is a guaranteed
  // miss on every step until someone notices.
  const missing = new Set();
  for (const question of questionsOf(preview, skills)) {
    for (const skill of question.skills || []) {
      if (!skills.includes(skill)) missing.add(skill);
    }
  }
  if (missing.size) {
    const names = [...missing].sort();
    out.push({
      id: "unselected-tags",
      tone: "error",
      title: "Some questions are tagged for skills this run cannot edit",
      body:
        `Questions in this selection are also tagged for ${list(names)}. Routing ` +
        "scores a question correct only when the agent opens exactly the skills it " +
        "is tagged for, and those descriptions are frozen — so those questions can " +
        "never score correct, and the optimizer will narrow the descriptions it " +
        `can edit trying to compensate. Select ${names.length > 1 ? "them" : "it"} ` +
        "too, or drop those questions.",
      skills: names,
    });
  }

  // A ticked skill with nothing tagged for it is shown to the analyst in full
  // and is rewritable, so it does not sit the run out — it gets edited from
  // other skills' evidence and then scored.
  const empty = skills.filter((skill) => {
    const group = (preview?.groups || []).find((g) => g.skill_name === skill);
    return group && !(group.questions || []).length;
  });
  if (empty.length) {
    out.push({
      id: "skill-without-questions",
      tone: "warning",
      title: "A selected skill has no questions",
      body:
        `No question in these eval sets is tagged for ${list(empty)}. Its ` +
        "description is still shown to the optimizer and can still be rewritten, " +
        "so it will be edited from the other skills' evidence and then graded on " +
        "questions it never saw. Untick it, or tag some questions for it.",
      skills: empty,
    });
  }
  return out;
}

/**
 * What is wrong with the *numbers*, for the last screen before Start.
 *
 * `values` carries nulls for fields being typed, so every rule checks for a
 * real number first: warning about an edit budget below the skill count while
 * someone is halfway through clearing the field is how a useful banner becomes
 * one people learn to scroll past.
 */
export function routingReviewWarnings({ mode, skills = [], split, values = {} }) {
  const out = [];
  if (mode !== "routing" || !skills.length || !split) return out;

  const n = skills.length;
  const trainSize = (split.train || []).length;
  const valSize = (split.val || []).length;
  const budget = values.learning_rate;
  const batch = values.batch_size;

  if (Number.isFinite(budget) && budget < n) {
    out.push({
      id: "edit-budget",
      tone: "warning",
      title: "The edit budget is smaller than the number of skills",
      body:
        `One description is one edit, so ${n} skills need at least ${n}. Moving a ` +
        "boundary takes two — narrowing one description and widening the one that " +
        "should catch what it drops — and when the budget cuts the pool, half a " +
        "paired edit is applied: a class of questions ends up claimed by nobody. " +
        `Raise it to at least ${n}.`,
    });
  }

  if (Number.isFinite(batch) && batch > 0 && batch / n < MIN_QUESTIONS_PER_SKILL) {
    out.push({
      id: "thin-batch",
      tone: "warning",
      title: "Each step shows the optimizer very few questions per skill",
      body:
        `${batch} questions across ${n} skills is about ${Math.floor(batch / n)} each. ` +
        "A description is rewritten whole from what its step saw, so at this size a " +
        "single failure is most of the evidence behind the rewrite — which is what " +
        "makes a routing run swing rather than settle.",
    });
  }

  const suggestion = suggestedBatchSize(trainSize, skills);
  if (Number.isFinite(batch) && batch > 0 && suggestion && batch < suggestion) {
    out.push({
      id: "batch-suggestion",
      tone: "info",
      title: `Consider a batch size of ${suggestion}`,
      body:
        `Routing wants about ${SUGGESTED_QUESTIONS_PER_SKILL} questions per skill in ` +
        `a step, which is ${suggestion} for ${n} skills against a training split of ` +
        `${trainSize}. Each step is sampled so that every skill is represented, so ` +
        "this is about how much evidence each description is rewritten from rather " +
        "than about covering the split — the epoch does that.",
      suggestion,
    });
  }

  if (values.gate_metric === "hard" && valSize && valSize < SMALL_VAL_SPLIT) {
    out.push({
      id: "hard-gate-small-val",
      tone: "warning",
      title: "A strict set match over a small validation split gives the gate little to compare",
      body:
        `"Exactly the right skills" is all-or-nothing per question, so over ` +
        `${valSize} validation questions the score moves in steps of 1/${valSize} and ` +
        "can sit at zero for the first several — and the gate keeps a candidate only " +
        "when it is strictly better, so every one gets rejected. F1 scores partial " +
        "credit, which gives it a direction to follow.",
    });
  }

  // The one failure on this screen that is not a matter of degree. Routing is
  // scored against each question's own skill tags, read back out of its trace,
  // so a validation question that carries no tags or produces no trace
  // contributes nothing to the baseline. When *every* one of them is like that
  // the engine cannot measure a baseline at all and aborts the run before step
  // 1 (`optimizer/engine.py`, "the baseline routing accuracy could not be
  // measured"). On a split of twenty that takes a broken eval set; on a split
  // of two it takes one untagged question, which is why this only appears down
  // here — the split floor is 1, so this is now reachable by ordinary use.
  if (valSize && valSize <= TINY_VAL_SPLIT) {
    out.push({
      id: "tiny-val-routing",
      tone: "warning",
      title: "A validation split this small can stop the run before it starts",
      body:
        `Routing is graded against each question's own skill tags, so a ` +
        `validation question with no tags — or one whose trace does not come ` +
        `back — counts for nothing. With ${valSize} of them, one such question ` +
        "is most of the gate and all of them being like that aborts the run " +
        "rather than scoring it. Check that every question in the Validation " +
        "column is tagged for the skills it belongs to.",
    });
  }

  if (n === 1) {
    out.push({
      id: "single-skill",
      tone: "info",
      title: "Only one description will move",
      body:
        "Descriptions compete, so a boundary is normally moved by writing both " +
        "sides of it. With one skill selected the others are frozen, which still " +
        "works but is a weaker lever — and it is the setup where narrowing a " +
        "description until it wins nothing is easiest to do by accident.",
    });
  }

  return out;
}

function list(names) {
  if (names.length === 1) return names[0];
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}
