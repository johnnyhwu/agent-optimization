// What the settings page shows, as opposed to what it stores.
//
// `settings_fields.js` owns the three modes and the percent conversion — the
// rules about a *value*. This owns the rules about the *page*: which rows a
// search box leaves standing, which groups still have anything in them, how
// many of a group's rows this developer has an opinion about, and — the one
// that changes the page's behaviour rather than its appearance — which edits
// have not been saved yet.
//
// It lives here rather than inside the panel for the reason every rule in this
// frontend does: `node --test` can load a module and cannot load JSX, so a rule
// left in a component is a rule with no test. The unsaved-edit comparison is
// the one that most needs one — it decides whether a person is warned before
// they throw their typing away.

import { OFF, SET, SYSTEM } from "./settings_fields.js";

// One control's state reduced to "what is this field actually saying", so two
// states can be compared for sameness.
//
// `null` is "nothing" — no opinion. It has to collapse three spellings of
// nothing into one: the system mode, a box holding an empty string, and a box
// holding spaces. Otherwise clicking into a blank field and pressing space
// would count as an unsaved change, and the page would warn about losing it.
export function saying(spec, entry) {
  if (!entry || entry.mode === SYSTEM) return null;
  if (entry.mode === OFF) return "off";
  if (spec.kind === "bool") return `set:${entry.raw}`;
  const text = String(entry.raw ?? "").trim();
  return text === "" ? null : `set:${text}`;
}

// Whether this row is overriding the deployment at all.
export function isOverridden(spec, entry) {
  return saying(spec, entry) !== null;
}

// The keys edited since the page last agreed with the server.
//
// The page used to show one number — how many settings were overridden — which
// is a fact about the saved state and says nothing about the form in front of
// you. A person who had typed into four boxes and a person who had typed into
// none saw the same sentence, and the Save button looked equally idle to both.
export function dirtyKeys(catalog, form, baseline) {
  const out = [];
  for (const spec of catalog) {
    const now = saying(spec, (form || {})[spec.key]);
    const before = saying(spec, (baseline || {})[spec.key]);
    if (now !== before) out.push(spec.key);
  }
  return out.sort();
}

// --- Search ------------------------------------------------------------------

// A row matches when every word typed appears somewhere a person could
// reasonably expect it to: the label they read, the help under it, the group it
// sits in, or the key itself — which is what an admin comparing this page
// against a deployment's environment variables will type.
export function matchesQuery(spec, groupLabel, query) {
  const terms = String(query || "").toLowerCase().split(/\s+/).filter(Boolean);
  if (terms.length === 0) return true;
  const haystack = [spec.label, spec.help, spec.key, groupLabel]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return terms.every((term) => haystack.includes(term));
}

// The page's body and its jump list are the same answer asked twice, so they
// are computed once: groups in catalogue order, each with the rows that survived
// the search, and empty groups dropped rather than rendered as a heading with
// nothing under it.
export function visibleGroups(catalog, groups, form, query) {
  const out = [];
  for (const group of groups || []) {
    const specs = (catalog || []).filter(
      (spec) => spec.group === group.id && matchesQuery(spec, group.label, query)
    );
    if (specs.length === 0) continue;
    out.push({
      ...group,
      specs,
      // Counted over the whole group, not over what the search left visible: a
      // count that changed as you typed would be reporting on the query rather
      // than on the settings.
      overridden: (catalog || []).filter(
        (spec) => spec.group === group.id && isOverridden(spec, (form || {})[spec.key])
      ).length,
    });
  }
  return out;
}

// --- The action bar's sentence ------------------------------------------------

// One line that has to answer three questions at once: is there anything to
// save, is anything stopping me, and if not, what is the state of this page.
//
// Errors win over the count, because an error is the reason the button is grey
// and a disabled button with no explanation is the thing this replaces.
export function barMessage({ dirty = 0, errors = 0, overridden = 0, saving = false } = {}) {
  if (saving) return "Saving…";
  if (errors > 0) {
    return `Fix ${errors} field${errors === 1 ? "" : "s"} before saving`;
  }
  if (dirty > 0) {
    return `${dirty} unsaved change${dirty === 1 ? "" : "s"}`;
  }
  if (overridden > 0) {
    return `Saved · ${overridden} setting${overridden === 1 ? "" : "s"} overridden`;
  }
  return "Following this deployment on everything";
}
