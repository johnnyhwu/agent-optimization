import { plural, pluralise } from "./plural.js";

// Do the questions in this eval set depend on skills the target agent actually
// has?
//
// The check exists because Decision 6 treats a question's skill tag and the
// agent's skill directory as the *same name*, and nothing until now proved it
// before a run. A question tagged `billling` is handed to an agent that has
// never heard of it, answers badly, and the trace shows an agent doing its best
// without the playbook it needed — a failure that looks like the agent being
// wrong rather than like a typo in a spreadsheet.
//
// Two decisions worth stating, because both could reasonably have gone the
// other way:
//
//   * **A missing skill warns; it never blocks.** Tags legitimately go missing —
//     an agent may route by another name, a set may be half-tagged — whereas an
//     unreachable agent is a certainty. Blocking on a warning that is sometimes
//     wrong is how people learn to click past it.
//   * **Matching is exact.** It has to be: exact is what the agent does. But a
//     tag that differs from a real skill only by case is called out by name,
//     because it is the one kind of miss that looks correct at a glance and is
//     the cheapest to fix.

/**
 * @param {{skill_name: string, question_count: number}[]} evalSetSkills
 * @param {string[]} agentSkills
 */
export function skillCoverage(evalSetSkills, agentSkills) {
  const tagged = evalSetSkills || [];
  const onAgent = agentSkills || [];
  // Lower-cased index for the near-miss hint only. The decision itself is made
  // on the exact names.
  const byLower = new Map(onAgent.map((name) => [name.toLowerCase(), name]));
  const exact = new Set(onAgent);

  const matched = [];
  const missing = [];
  for (const entry of tagged) {
    const name = entry.skill_name;
    if (exact.has(name)) {
      matched.push(name);
      continue;
    }
    const nearby = byLower.get(name.toLowerCase());
    missing.push({
      skill_name: name,
      question_count: entry.question_count ?? 0,
      // The agent's spelling of the same word, when that is all that differs.
      caseMatch: nearby && nearby !== name ? nearby : null,
    });
  }

  return { matched, missing, ok: missing.length === 0 };
}

/**
 * The whole warning — heading and body — or `null` when there is nothing to say.
 *
 * **The two are produced here together, deliberately.** They were briefly
 * produced apart, with the heading hardcoded at the call site, and that let the
 * heading make a claim the body did not support: an eval set with no tags at
 * all, against a perfectly healthy agent, was told "Some questions need skills
 * this agent does not have" — an accusation about the agent, on evidence that
 * says nothing about the agent. A warning that overstates itself once is read as
 * noise from then on, including on the run where it is right.
 *
 * `untagged` is counted by the server rather than derived here: a question may
 * carry two tags, so "questions minus tags" is not it.
 */
export function coverageWarning(coverage, untagged = 0) {
  const missing = coverage?.missing || [];
  const text = coverageText(coverage, untagged);
  if (!text) return null;
  return {
    // The more serious claim wins the heading when both apply; the body still
    // carries both sentences.
    title: missing.length
      ? "Some questions need skills this agent does not have"
      : "Some questions could not be checked",
    text,
  };
}

function coverageText(coverage, untagged) {
  const parts = [];

  for (const miss of coverage?.missing || []) {
    const depends =
      `${plural(miss.question_count, "question")} ` +
      `${pluralise(miss.question_count, "depends", "depend")} on it`;
    parts.push(
      miss.caseMatch
        ? `This agent has no skill named “${miss.skill_name}” — it spells it ` +
          `“${miss.caseMatch}”, and the two are matched exactly (${depends}).`
        : `This agent has no skill named “${miss.skill_name}” (${depends}).`
    );
  }

  // Stated plainly rather than folded into the sentence above, because it is a
  // different claim: not "something is missing" but "this check could say
  // nothing about these". A set where only two of sixty questions are tagged
  // would otherwise come back clean.
  if (untagged > 0) {
    parts.push(
      `${plural(untagged, "question")} ${pluralise(untagged, "has", "have")} no ` +
        `skill tag, so nothing was checked for ${pluralise(untagged, "it", "them")}.`
    );
  }

  return parts.length ? parts.join(" ") : null;
}
