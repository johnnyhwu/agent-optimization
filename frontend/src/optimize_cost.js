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

export function estimateRun({
  nTrain, nVal, epochs, batchSize, minibatchSize,
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

  // Reflect splits failures and successes into separate minibatches, so a batch
  // produces one more group than dividing it once would suggest as soon as it
  // contains one of each — which is the ordinary case, and stays true even when
  // the minibatch size is larger than the whole batch. The worst case is the
  // honest number here: an estimate that undersells the model everything is
  // billed on is the one that causes trouble.
  const analystPerStep = Math.ceil(perStep / group) + (perStep >= 2 ? 1 : 0);
  // Plus one merge (aggregate) and one ranking (clip) per step.
  const optimizerCallsMax = totalSteps * (analystPerStep + 2);

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

function count(value) {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0;
}
