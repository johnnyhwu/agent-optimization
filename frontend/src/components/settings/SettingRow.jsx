import React from "react";
import Field from "../ui/Field.jsx";
import { OFF, SET, SYSTEM, placeholder } from "../../settings_fields.js";

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
export default function SettingRow({ spec, entry, system, error, isNew, onChange }) {
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
      <Field label={label} help={spec.help} error={error}>
        <Segmented spec={spec} entry={entry} system={system} onChange={onChange} />
      </Field>
    );
  }

  return (
    <Field label={label} help={spec.help} error={error} hint={hintFor(spec)}>
      <input
        type="text"
        inputMode={spec.kind === "text" ? undefined : "decimal"}
        value={entry.raw}
        placeholder={placeholder(spec, system)}
        aria-invalid={error ? "true" : undefined}
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

function hintFor(spec) {
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
      <div className="setting-seg" role="group">
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
