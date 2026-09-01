// What the wizard promises before anyone presses Start.
//
// Stated in **calls**, never in money. This platform never sees a price list —
// the models are whatever OpenAI-compatible base URL the developer pointed it
// at, and their rates are theirs to know. A number with a currency symbol on it
// would be trusted in a way a made-up number must not be, so the split is:
// we count the calls, they know what a call costs.
//
// Three counts rather than one total, because they are bought from three
// different places and the expensive one is not the biggest one. A run does
// thousands of agent and judge calls on a small model; it does a few dozen
// optimizer calls on the largest model available, with a whole minibatch of
// truncated traces in each prompt.

/** How many analyst calls one step buys, at worst.
 *
 * Reflect splits failures and successes into separate minibatches, so a batch
 * produces one more group than dividing it once would suggest as soon as it
 * contains one of each — which is the ordinary case, and stays true even when
 * the minibatch size is larger than the whole batch. The worst case is the
 * honest number: an estimate that undersells the model everything is billed on
 * is the one that causes trouble.
 *
 * Exported because the wizard says it in words beside the field ("a batch of 16
 * in groups of 8 is up to 3 calls"), and two implementations of the same
 * sentence would eventually quote two different numbers on one screen.
 *
 * Routing is one call, and neither the split nor the minibatch size applies to
 * it. Its parameter is a single line of frontmatter, so every group would
 * return a complete rewrite of that one line and the merge stage would be
 * choosing between them having seen the edits and none of the questions — and
 * its failures and successes are the two sides of one boundary, which is not a
 * thing two analysts can each be shown half of.
 */
export function analystCallsPerStep(questionsPerStep, minibatchSize, mode = "isolated") {
  const perStep = Math.max(0, count(questionsPerStep));
  if (!perStep) return 0;
  if (mode === "routing") return 1;
  const group = Math.max(1, count(minibatchSize));
  return Math.ceil(perStep / group) + (perStep >= 2 ? 1 : 0);
}

export function estimateRun({
  nTrain, nVal, epochs, batchSize, minibatchSize, mode = "isolated",
} = {}) {
  const train = count(nTrain);
  const val = count(nVal);
  const passes = Math.max(1, count(epochs));
  const batch = Math.max(1, count(batchSize));
  const group = Math.max(1, count(minibatchSize));

  const stepsPerEpoch = train ? Math.max(1, Math.ceil(train / batch)) : 0;
  const totalSteps = stepsPerEpoch * passes;
  // A step never answers more questions than the split holds, however large a
  // batch size was typed into the box above.
  const perStep = Math.min(batch, train);

  // The baseline measures validation once before any editing; every step after
  // it answers its batch and then the whole validation split again.
  const agentCalls = val + totalSteps * (perStep + val);

  const analystPerStep = analystCallsPerStep(perStep, group, mode);
  // Plus one merge (aggregate) and one ranking (clip) per step — except in
  // routing, where the single analyst patch is returned untouched by both
  // (`_hierarchical_merge` short-circuits a one-element list, `rank_and_select`
  // a pool already inside its budget) without the model being called at all.
  const stagesPerStep = mode === "routing" ? 0 : 2;
  const optimizerCallsMax = totalSteps * (analystPerStep + stagesPerStep);

  return {
    stepsPerEpoch,
    totalSteps,
    questionsAnswered: agentCalls,
    agentCalls,
    // One judge per answered question. Named separately because it is the cost
    // most often left out of a plan, and leaving it out halves the estimate.
    judgeCalls: agentCalls,
    optimizerCallsMax,
  };
}

// The arithmetic behind each number, in words, for the `?` beside it.
//
// Written from the same inputs the estimate was computed from rather than
// hand-written prose beside it, so a change to the formula above cannot leave a
// confident explanation of the old one on the screen. The review card is the
// last thing read before an hour of calls is authorised, and "≈ 1,240" with no
// derivation is a number nobody can check — the two questions it always draws
// are "does that include validation?" and "is that per epoch?".
export function explainRun({
  nTrain, nVal, epochs, batchSize, minibatchSize, mode = "isolated",
} = {}) {
  const train = count(nTrain);
  const val = count(nVal);
  const passes = Math.max(1, count(epochs));
  const batch = Math.max(1, count(batchSize));
  const group = Math.max(1, count(minibatchSize));
  const e = estimateRun({ nTrain, nVal, epochs, batchSize, minibatchSize, mode });
  const perStep = Math.min(batch, train);
  const analystPerStep = analystCallsPerStep(perStep, group, mode);
  const routing = mode === "routing";

  return {
    steps:
      `${train} training questions ÷ ${batch} per batch = ${e.stepsPerEpoch} `
      + `step(s) per epoch, × ${passes} epoch(s) = ${e.totalSteps}.`,
    agentCalls:
      `${val} for the baseline measurement, then each of the ${e.totalSteps} `
      + `step(s) answers ${perStep} training question(s) and re-answers all `
      + `${val} validation question(s): ${val} + ${e.totalSteps} × `
      + `(${perStep} + ${val}) = ${e.agentCalls.toLocaleString()}.`,
    judgeCalls:
      "One judge call grades one agent answer, so this is the agent count "
      + `again: ${e.judgeCalls.toLocaleString()}. It is named separately `
      + "because leaving it out halves an estimate.",
    optimizerCalls: routing
      ? `Per step: one analyst call carrying all ${perStep} question(s) at once, `
        + "and no merge or ranking — with a single patch both stages return their "
        + `input untouched without calling the model. ${e.totalSteps} × 1 = `
        + `${e.optimizerCallsMax.toLocaleString()}. These run on the largest model `
        + "configured, so this small number is usually the large bill."
      : `Per step: ${analystPerStep} analyst call(s) — ${perStep} question(s) in `
        + `minibatches of ${group}, with failures and successes reflected on `
        + "separately — plus one merge and one ranking. "
        + `${e.totalSteps} × (${analystPerStep} + 2) = `
        + `${e.optimizerCallsMax.toLocaleString()} at most. These run on the `
        + "largest model configured and each carries a minibatch of traces, so "
        + "this small number is usually the large bill.",
  };
}

function count(value) {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0;
}
