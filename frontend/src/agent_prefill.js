// Opening a form on the developer's own agent, rather than on nothing.
//
// The settings page is where somebody says "my agent lives here" once
// (`settings_catalog.py`, the `agent` group), and the backend already lays those
// values over the deployment's own in every defaults response —
// `/run-config/defaults` and `/optimization/defaults` both return them. The run
// dialog and the playground read that. Two screens did not: the contract
// checker started every field at `""`, and the Optimize wizard only ever wrote
// its config from a restored draft. Both asked for an address their user had
// already given us.
//
// The rule is small and the same in both places, so it lives here rather than
// twice inside two components — and here it can be tested, which matters because
// getting it wrong in the other direction destroys work.
//
// **Only into an empty field.** Same principle as the skills-URL guess in
// `AgentEndpointsFields`: prefilling is a convenience, and a convenience that
// overwrites what somebody typed is not one. It is also what makes the order of
// the wizard's two writes irrelevant — a restored draft is not empty, so a
// defaults response arriving after it cannot undo it.
//
// **Never a credential.** The keys are named rather than copied wholesale.
// `agent_api_key` is write-only on the server and is never in a defaults
// response, but a list that says which keys travel is the thing that stays true
// when somebody adds a field to the catalogue.

export const AGENT_PREFILL_KEYS = [
  "agent_chat_url",
  "agent_skills_url",
  "agent_auth_header",
  "agent_timeout_s",
];

// Blank, for a field that has not been filled in.
//
// `0` is not blank. It is a nonsense timeout and the form will reject it, but it
// is an answer somebody typed, and replacing it with the environment's 60 would
// be this function editing a value rather than filling a gap.
function empty(value) {
  if (value === undefined || value === null) return true;
  return typeof value === "string" && value.trim() === "";
}

/**
 * `current`, with every blank agent field filled in from `defaults`.
 *
 * Returns a new object — callers hand the result straight to a `setState`, and
 * one that mutated its argument would be a store of React state edited in place.
 */
export function prefillAgent(current, defaults) {
  const out = { ...(current || {}) };
  if (!defaults) return out;
  for (const key of AGENT_PREFILL_KEYS) {
    if (!empty(out[key])) continue;
    if (empty(defaults[key])) continue;
    out[key] = defaults[key];
  }
  return out;
}
