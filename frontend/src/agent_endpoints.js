// What the two agent endpoints prove, and what each screen may do about it.
//
// An agent server is named by two URLs now: a chat endpoint that answers
// questions, and an optional skills endpoint that lists the files it is running
// with. Four checks run against them (`services/agent_probe.py` on the backend):
//
//   skills    the listing can be read
//   chat      the chat endpoint answers
//   override  the skills we sent actually took effect
//   trace     that call's trace can be read back
//
// Every check is a **tri-state**: `true`, `false`, or `null` for "attempted,
// and there was nothing to attempt". That third state is the whole reason this
// module exists. "The override did not apply" and "we did not test the
// override" are different facts, and a screen that draws them the same either
// blocks a working agent or starts an optimization run that will measure
// nothing.
//
// A check that is **absent from the object** is a fourth thing again: nobody has
// run it yet. That is what lets a form stay pressable before a model call has
// been spent on it. The distinction matters most for `skills`, where `null`
// means "no skills endpoint is configured" — a settled fact, and a blocking one
// for optimization — while absent means the probe has not come back.
//
// The three screens want different things from the same four answers, and the
// difference is not arbitrary:
//
//   * **Evaluation** needs an agent that answers. It never sends an override and
//     never reads a trace, so it cannot be blocked by either. A skills endpoint
//     is a bonus — it buys the coverage warning — so a broken one warns.
//   * **Playground** needs an agent that answers. An override that did not land
//     matters, but not enough to lock the screen: asking questions of the
//     deployed skills is still useful, and the check has real false positives
//     (a refusal, a tool that did not load). So it warns.
//   * **Optimization** needs all four. Every rollout sends a candidate file set,
//     and the run's own pre-flight proves the agent used it by reading the
//     trace — so without the override or the trace the run spends an hour
//     measuring the deployed skill and reports the flat line as a finding.

export const FEATURES = ["evaluation", "playground", "optimization"];

// Which checks each feature blocks on, warns about, and ignores. Data rather
// than branches, because this table *is* the policy — and the reasoning for
// every cell is in the comment above, not spread across three components.
const POLICY = {
  evaluation: { block: ["chat"], warn: ["skills"] },
  playground: { block: ["chat"], warn: ["skills", "override"] },
  optimization: { block: ["chat", "skills", "override", "trace"], warn: [] },
};

const REASONS = {
  chat: "This agent's chat endpoint did not answer.",
  skills: "This agent's skills endpoint could not be read.",
  override:
    "This agent did not appear to use the skill files we sent, so an " +
    "optimization run would measure its deployed skills instead.",
  trace:
    "This call's trace could not be read back, and an optimization run needs " +
    "traces to tell whether a candidate skill was used.",
};

const MISSING = {
  skills:
    "This agent has no skills endpoint, so there is nothing to read its skill " +
    "files from.",
};

// The chat endpoint's conventional path, and the skills path most implementers
// pick beside it. Used only to prefill.
const CHAT_SUFFIX = "/v1/chat/completions";

/**
 * A skills URL to prefill from a chat URL, or "" when there is nothing to guess.
 *
 * Offered, never applied silently: it is a shortcut for the common layout, and
 * a wrong guess left in a field reads as a value somebody chose. Only the
 * conventional suffix is rewritten — anything else, and we have no idea where
 * that server keeps its skills.
 */
export function deriveSkillsUrl(chatUrl) {
  const url = (chatUrl || "").trim().replace(/\/+$/, "");
  if (!url) return "";
  if (!url.toLowerCase().endsWith(CHAT_SUFFIX)) return "";
  return `${url.slice(0, -CHAT_SUFFIX.length)}/skills`;
}

/**
 * The tier a set of checks adds up to: 0, 1 or 2.
 *
 * A label for people, not a gate — `gateFor` decides what is allowed. It exists
 * because "chat works, skills do not, override untested" is four facts, and
 * nobody reads four facts off a form. An unattempted check never counts as
 * passed: a tier is a claim about what was proven.
 */
export function tierOf(checks = {}) {
  if (checks.chat?.ok !== true) return 0;
  if (checks.skills?.ok !== true) return 0;
  if (checks.override?.ok === true && checks.trace?.ok === true) return 2;
  return 1;
}

export const TIER_LABELS = {
  0: "Evaluation only",
  1: "Evaluation and playground",
  2: "Everything, including optimization",
};

/**
 * May this feature start, and what should be said either way.
 *
 * Returns `{ blocked, reason, warnings }`. `reason` is the one sentence a
 * disabled button explains itself with; `warnings` are the things worth saying
 * that are not worth stopping for.
 *
 * A check that is absent blocks nothing. That is what lets the Run-eval dialog
 * stay pressable before anyone has spent a model call on the chat probe: the
 * dialog asks on the way past instead.
 */
export function gateFor(feature, checks = {}) {
  const policy = POLICY[feature];
  if (!policy) throw new Error(`unknown feature: ${feature}`);

  const failed = (name) => checks[name]?.ok === false;
  // A skills endpoint that was never configured is not a failed check, but for
  // a feature that blocks on it the outcome is the same and the sentence must
  // not be. "Could not be read" sends someone to debug a server that is fine.
  //
  // `name in checks` is load-bearing: it separates "we asked, and there is no
  // URL" from "we have not asked". Without it, a form blocks itself before its
  // first probe has even been sent.
  const missing = (name) =>
    name === "skills" && "skills" in checks && checks.skills?.ok === null;

  const blocking = policy.block.filter((n) => failed(n) || missing(n));
  return {
    blocked: blocking.length > 0,
    reason: blocking.length
      ? (missing(blocking[0]) ? MISSING[blocking[0]] : REASONS[blocking[0]])
      : "",
    warnings: policy.warn.filter(failed).map((n) => REASONS[n]),
  };
}

/**
 * Is a chat probe result still about the URLs on screen?
 *
 * The probe costs a model call, so its answer is cached until something makes
 * it untrue. Editing the URL is the thing that makes it untrue — and an answer
 * about the previous address, shown beside the new one, is worse than no answer
 * at all, because it is indistinguishable from a check that passed.
 */
export function probeMatches(probe, { chatUrl, skillsUrl }) {
  if (!probe) return false;
  return (
    (probe.forChatUrl || "") === (chatUrl || "") &&
    (probe.forSkillsUrl || "") === (skillsUrl || "")
  );
}

/**
 * Does this failure look like a server asking to be authenticated?
 *
 * Used to open the credential panel on the screen the refusal appeared on.
 * That is the whole of how an optional feature gets found: nobody opens a
 * folded panel looking for a field they have no reason to believe exists, and
 * "401" beside a URL is not an instruction.
 *
 * Matched on the backend's own hint rather than on the status code, so the two
 * cannot disagree about what counts — `services/agent_probe.py` decides, and
 * this reads the decision.
 */
export function looksUnauthorized(check) {
  const error = check?.error || "";
  return error.includes("requires a credential") || error.includes("was refused");
}

/**
 * Will the agent's credential be sent to this skills endpoint too?
 *
 * The rule is the backend's (`integrations/real/agent_auth.py:same_origin`) and
 * this is the copy that explains it on screen — a developer whose skills
 * endpoint is on another host would otherwise see a 401 there and no reason for
 * it, having just entered a key that works.
 */
export function credentialReachesSkills(chatUrl, skillsUrl) {
  const origin = (url) => {
    try {
      const u = new URL((url || "").trim());
      // Comparing `origin` rather than the parts: it already normalises the
      // default port away, which is the case a string comparison gets wrong.
      return u.origin;
    } catch {
      return "";
    }
  };
  const a = origin(chatUrl);
  return Boolean(a) && a === origin(skillsUrl);
}

/**
 * A check's error split into what the server said and what to do about it.
 *
 * The backend joins the two with a blank line (`services/agent_probe.py`), and
 * HTML collapses that into a single space — so a 401 and the sentence telling
 * you to add an API key ran together into one long line, with the advice least
 * likely to be read at the end of it. Returns `{ message, hint }`, `hint` being
 * "" when there is nothing beyond the server's own words.
 */
export function splitHint(error) {
  const text = error || "";
  const at = text.indexOf("\n\n");
  if (at === -1) return { message: text, hint: "" };
  return { message: text.slice(0, at).trim(), hint: text.slice(at + 2).trim() };
}
