// The settings page's state machine, which is smaller than it sounds.
//
// A field is blank until you type in it, and blank means "I have no opinion —
// use whatever the deployment says". The presence of text *is* the override.
// That is why the page has no "yours / theirs" markers and no reset buttons:
// clearing the box is the reset, and an empty box with the deployment's value
// showing through as a placeholder says everything a badge would have said.
//
// Two kinds of field cannot say that with a blank, and they are the whole
// reason this module has three modes rather than two:
//
//   * a checkbox has no empty state. `diagnosis_enabled`, `slow_update` and
//     `meta_skill` need to distinguish "follow the deployment" from "off", and
//     an unticked box cannot. They get three positions.
//   * `early_stop_target_score` already uses blank to mean "aim at nothing".
//     Overloading it to also mean "no opinion" would take away the only way to
//     say the first one.
//
// The percent conversion is the other trap. The Optimize wizard types these
// error shares as whole percents and the API stores them as fractions
// (`HYPER_FIELDS[...].scale` in optimize_wizard.js). A settings page that did
// its own arithmetic would be wrong by a factor of a hundred in whichever
// direction nobody tested; here the catalogue marks such a key `fraction` and
// `settings_catalog.test.js` checks that marking against the wizard's own scale.

export const SYSTEM = "system";
export const SET = "set";
export const OFF = "off";

// Stored 0..1, typed as a whole percent.
const PERCENT = 100;

function unround(value) {
  // 0.1 * 100 is 10.000000000000002. The page would show that.
  return Number(value.toPrecision(12));
}

function specOf(catalog, key) {
  return catalog.find((s) => s.key === key);
}

// --- Reading -----------------------------------------------------------------

// One stored value as the control's state. A key the user has no opinion about
// is absent from `values` — never present-and-empty, which is what lets `null`
// mean "off" for the one field that needs it.
export function fieldFromStored(spec, values) {
  if (!Object.prototype.hasOwnProperty.call(values, spec.key)) {
    return { mode: SYSTEM, raw: "" };
  }
  const value = values[spec.key];
  if (value === null) {
    // A null for a field with no "off" is a stored value that has stopped making
    // sense; fall back rather than render a control in an impossible state.
    return spec.optional ? { mode: OFF, raw: "" } : { mode: SYSTEM, raw: "" };
  }
  if (spec.kind === "bool") return { mode: SET, raw: value ? "true" : "false" };
  if (spec.kind === "fraction") {
    return { mode: SET, raw: String(unround(value * PERCENT)) };
  }
  return { mode: SET, raw: String(value) };
}

export function fromStored(catalog, values) {
  const form = {};
  for (const spec of catalog) form[spec.key] = fieldFromStored(spec, values || {});
  return form;
}

// --- Parsing -----------------------------------------------------------------

const ok = (value) => ({ ok: true, value });
const bad = (error) => ({ ok: false, error });

export function parse(spec, raw) {
  const text = String(raw ?? "").trim();

  if (spec.kind === "text") return ok(String(raw ?? ""));
  if (spec.kind === "bool") {
    if (text === "true") return ok(true);
    if (text === "false") return ok(false);
    return bad("must be on or off");
  }

  if (spec.kind === "int" && !/^-?\d+$/.test(text)) {
    return bad(text === "" ? "enter a whole number" : "must be a whole number");
  }
  const typed = Number(text);
  if (text === "" || !Number.isFinite(typed)) return bad("must be a number");

  // A percent on the way in, a fraction on the way out — and the bounds in the
  // catalogue are the stored ones, so the conversion has to happen first.
  const value = spec.kind === "fraction" ? unround(typed / PERCENT) : typed;

  if (spec.minimum !== null && spec.minimum !== undefined && value < spec.minimum) {
    return bad(`must be at least ${display(spec, spec.minimum)}`);
  }
  if (spec.maximum !== null && spec.maximum !== undefined && value > spec.maximum) {
    return bad(`must be at most ${display(spec, spec.maximum)}`);
  }
  return ok(value);
}

// --- Writing -----------------------------------------------------------------

// Whether this control is saying anything at all. Blank and whitespace are not
// opinions; a boolean or an explicit "off" is.
function speaks(spec, entry) {
  if (!entry || entry.mode === SYSTEM) return false;
  if (entry.mode === OFF) return true;
  if (spec.kind === "bool") return true;
  return String(entry.raw ?? "").trim() !== "";
}

function walk(catalog, form, onValue, onError) {
  for (const spec of catalog) {
    const entry = (form || {})[spec.key];
    if (!speaks(spec, entry)) continue;
    if (entry.mode === OFF) {
      if (!spec.optional) {
        onError(spec, `${spec.key} has no "off" setting`);
        continue;
      }
      onValue(spec, null);
      continue;
    }
    const parsed = parse(spec, entry.raw);
    if (parsed.ok) onValue(spec, parsed.value);
    else onError(spec, `${spec.key}: ${parsed.error}`);
  }
}

// What the form would save. Throws rather than dropping a bad field: a value the
// page accepted and the save quietly discarded is worse than an error message,
// because the user walks away believing it took.
export function overrides(catalog, form) {
  const out = {};
  walk(
    catalog,
    form,
    (spec, value) => {
      out[spec.key] = value;
    },
    (_spec, message) => {
      throw new Error(message);
    }
  );
  return out;
}

// The same walk, reporting instead of throwing, for rendering under each field.
export function errorsOf(catalog, form) {
  const errors = {};
  walk(
    catalog,
    form,
    () => {},
    (spec, message) => {
      errors[spec.key] = message.replace(`${spec.key}: `, "");
    }
  );
  return errors;
}

export function hasErrors(catalog, form) {
  return Object.keys(errorsOf(catalog, form)).length > 0;
}

// --- Showing ------------------------------------------------------------------

function display(spec, value) {
  if (value === null || value === undefined) return "";
  if (spec.kind === "fraction") return String(unround(value * PERCENT));
  if (spec.kind === "bool") return value ? "on" : "off";
  return String(value);
}

// What an empty box shows through: this deployment's value, in the units the box
// is typed in. It is the whole "I have not overridden this" state, so it has to
// be the real value rather than a hint like "e.g. 8".
export function placeholder(spec, systemValue) {
  return display(spec, systemValue);
}

// Which keys this form is overriding, for the "N changed" line on the page.
export function changedKeys(catalog, form) {
  const out = [];
  walk(catalog, form, (spec) => out.push(spec.key), () => {});
  return out.sort();
}

// Whether the values a defaults endpoint returned differ from what the
// deployment alone would have produced — the one line the three working pages
// show, in place of a marker on every field.
export function differsFromSystem(defaults, systemDefaults) {
  if (!defaults || !systemDefaults) return false;
  return Object.keys(systemDefaults).some(
    (key) => JSON.stringify(defaults[key]) !== JSON.stringify(systemDefaults[key])
  );
}

export { specOf };
