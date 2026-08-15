// What a run may be renamed to, in one place.
//
// A run's name is the only thing on the list that a human wrote, and it is how
// one run is told from another six weeks later. Both list surfaces — eval runs
// and optimization runs — now edit it inline, so the rule has to be one rule:
// two copies of "is this name allowed" drift, and the way they drift is that
// one surface accepts something the server then rejects, which the developer
// meets as a failed save with no explanation.
//
// Here rather than in the component because `node --test` loads pure modules
// and cannot load JSX — logic left inside a component is logic that is never
// tested. The server enforces the same limits again (a browser is not a
// validator), and `MAX_LENGTH` is deliberately the same number in both.

export const MAX_LENGTH = 120;

/** The name as it would be stored: trimmed, with an empty string meaning none.
 *
 * Empty is a legitimate value, not a rejection — clearing the name puts the row
 * back to the timestamp it falls back to, which is the only way to undo a
 * rename you regret.
 */
export function normalizeRunName(value) {
  return String(value ?? "").trim();
}

/** `null` when the name is fine, otherwise the sentence to show under the field. */
export function runNameError(value) {
  const name = normalizeRunName(value);
  if (name.length > MAX_LENGTH) {
    return `Too long — ${name.length} characters, and the limit is ${MAX_LENGTH}.`;
  }
  // A name made of control characters renders as nothing at all, so the row
  // would look unnamed while the server holds a value that is not empty — and
  // no amount of clicking the field would explain why.
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f]/.test(name)) {
    return "Line breaks and control characters are not allowed in a name.";
  }
  return null;
}

/** Whether saving would change anything.
 *
 * The submit is skipped entirely when it would not: pressing the tick without
 * having typed anything should close the editor, not spend a request and raise
 * a toast claiming a rename that renamed nothing.
 */
export function runNameChanged(current, next) {
  return normalizeRunName(current) !== normalizeRunName(next);
}
