import React from "react";
import Field from "../ui/Field.jsx";
import { OFF, SET, SYSTEM, placeholder } from "../../settings_fields.js";
import { isOverridden } from "../../settings_view.js";

// One setting, in the only two shapes it can take.
//
// A text or number field is **empty by default**, with this deployment's value
// showing through as the placeholder. That empty box is the "I have no opinion"
// state, so there is nothing to badge and no reset button to add — typing is how
// you override, clearing is how you undo. Grey text means the deployment's
// answer, black text means yours, and that is the whole legend.
//
// A checkbox cannot hold three states and this needs three: follow the
// deployment, force on, force off. `early_stop_target_score` needs three for a
// different reason — blank already means "aim at nothing", so it cannot also
// mean "no opinion". Both get a segmented control instead, which is the same
// idea drawn honestly rather than a checkbox that quietly loses one answer.
//
// Two things the legend above did not cover, and a page of twenty-five rows
// needs both:
//
//   *Which* rows are yours has to survive a scan. Grey-versus-black is a real
//   distinction when you are looking at one field and no distinction at all
//   when you are looking for one among twenty-five, so an overridden row also
//   carries a rule down its left edge — the same accent the jump list counts
//   with, so the two agree.
//
//   "Clearing the box is the reset" is true and undiscoverable. Selecting a URL
//   and deleting it is not something anyone tries in order to find out what
//   happens. The row says so, once, on the rows where it applies: `Reset` is
//   offered only when there is something to reset, and only on the typed
//   fields, because a segmented control's first position is already a visible
//   way back.
export default function SettingRow({ spec, entry, system, error, isNew, onChange }) {
  const id = `setting-${spec.key}`;
  const overridden = isOverridden(spec, entry);
  const className = `setting-row${overridden ? " is-overridden" : ""}`;

  const label = (
    <>
      {spec.label}
      {isNew && (
        <span className="setting-new" title="Added since you last looked here">
          new
        </span>
      )}
    </>
  );

  if (spec.kind === "bool" || spec.optional) {
    return (
      <Field label={label} help={spec.help} error={error} className={className}>
        <Segmented spec={spec} entry={entry} system={system} onChange={onChange} />
      </Field>
    );
  }

  const unit = unitFor(spec);
  const hint = (unit || overridden) && (
    <>
      {unit}
      {overridden && (
        <button
          type="button"
          className="setting-reset"
          onClick={() => onChange({ mode: SYSTEM, raw: "" })}
        >
          Reset
        </button>
      )}
    </>
  );

  return (
    <Field
      label={label}
      htmlFor={id}
      help={spec.help}
      error={error}
      hint={hint}
      className={className}
    >
      <input
        id={id}
        type="text"
        inputMode={spec.kind === "text" ? undefined : "decimal"}
        value={entry.raw}
        placeholder={placeholder(spec, system)}
        aria-invalid={error ? "true" : undefined}
        aria-describedby={
          error ? `${id}-error` : spec.help ? `${id}-help` : undefined
        }
        onChange={(e) => {
          const raw = e.target.value;
          // Cleared is not "set to empty" — it is the override going away. The
          // distinction matters: the save sends the whole set, so a key that
          // leaves the form is a key that leaves the database.
          onChange(raw.trim() === "" ? { mode: SYSTEM, raw: "" } : { mode: SET, raw });
        }}
      />
    </Field>
  );
}

function unitFor(spec) {
  if (spec.kind === "fraction") return "%";
  if (spec.key.endsWith("_s")) return "seconds";
  return undefined;
}

// Follow / on / off — or follow / off / a number, for the one optional field.
function Segmented({ spec, entry, system, onChange }) {
  const options = spec.kind === "bool"
    ? [
        { mode: SYSTEM, raw: "", label: followLabel(spec, system) },
        { mode: SET, raw: "true", label: "On" },
        { mode: SET, raw: "false", label: "Off" },
      ]
    : [
        { mode: SYSTEM, raw: "", label: followLabel(spec, system) },
        { mode: OFF, raw: "", label: "Off" },
        { mode: SET, raw: entry.mode === SET ? entry.raw : "", label: "Set to" },
      ];

  const current = options.findIndex(
    (o) => o.mode === entry.mode && (spec.kind === "bool" ? o.raw === entry.raw : true)
  );

  return (
    <div className="setting-seg-wrap">
      {/* Named, because "group" with no name is a landmark a screen reader
          announces as nothing at all — and out of the label's earshot these
          three buttons read as a bare "System, On, Off". */}
      <div className="setting-seg" role="group" aria-label={spec.label}>
        {options.map((option, index) => (
          <button
            key={option.label}
            type="button"
            className={`setting-seg-btn${index === current ? " is-on" : ""}`}
            aria-pressed={index === current}
            onClick={() => onChange({ mode: option.mode, raw: option.raw })}
          >
            {option.label}
          </button>
        ))}
      </div>
      {spec.kind !== "bool" && entry.mode === SET && (
        <input
          type="text"
          inputMode="decimal"
          className="setting-seg-value"
          aria-label={`${spec.label} value`}
          value={entry.raw}
          placeholder={placeholder(spec, system)}
          onChange={(e) => onChange({ mode: SET, raw: e.target.value })}
        />
      )}
      {spec.kind === "fraction" && entry.mode === SET && <span className="setting-seg-unit">%</span>}
    </div>
  );
}

// The first position says what following the deployment currently gets you, so
// the control is readable without looking anything up.
function followLabel(spec, system) {
  const shown = placeholder(spec, system);
  return shown ? `System (${shown})` : "System";
}
