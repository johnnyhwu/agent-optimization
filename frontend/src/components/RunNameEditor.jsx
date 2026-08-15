import React, { useEffect, useRef, useState } from "react";
import { IconButton } from "./ui/Button.jsx";
import { IconCheck, IconPencil, IconX } from "./icons.jsx";
import { runNameChanged, runNameError, MAX_LENGTH } from "../run_name.js";

// A run's name, editable where it is read.
//
// Both run lists show this. A name could only be given at the moment the run
// was triggered — the one moment nobody knows yet what the run will turn out to
// be about — so in practice almost every row fell back to its start time, and a
// column of timestamps tells you nothing about which run was which.
//
// The control is one button that changes what it is: a pencil while the name is
// just text, a tick while the field is open. That is the whole interaction, and
// it means the row never grows a second button that is meaningless most of the
// time. Escape and the cross both abandon; Enter and the tick both commit.
//
// The rules — trimming, the length limit, whether anything actually changed —
// live in `src/run_name.js` beside their tests, because this file cannot be
// tested at all (`node --test` loads pure modules, not JSX).
export default function RunNameEditor({
  name,
  fallback,
  canEdit = true,
  onRename,
  // The rail renders these inside a button, where a nested <button> is invalid
  // HTML and swallows the row's own click. There the name is read-only and the
  // pencil lives on the row's own controls instead.
  label = "run",
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(name || "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const inputRef = useRef(null);

  // A refetch while the field is closed should be reflected; one while it is
  // open must not overwrite what is being typed.
  useEffect(() => {
    if (!editing) setValue(name || "");
  }, [name, editing]);

  useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [editing]);

  function open(event) {
    event.stopPropagation();
    setError(null);
    setValue(name || "");
    setEditing(true);
  }

  function cancel() {
    setEditing(false);
    setError(null);
    setValue(name || "");
  }

  async function commit(event) {
    event?.stopPropagation?.();
    const problem = runNameError(value);
    if (problem) {
      setError(problem);
      return;
    }
    // Pressing the tick without having typed anything closes the field. Saving
    // would spend a request and raise a toast for a rename that renamed nothing.
    if (!runNameChanged(name, value)) {
      cancel();
      return;
    }
    setSaving(true);
    try {
      await onRename(value.trim());
      setEditing(false);
      setError(null);
    } catch (e) {
      // Inline, not a toast: the field that has to change is right here, and a
      // toast about a name is read after the field it refers to has closed.
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }

  if (!editing) {
    return (
      <span className="run-name">
        <span className="run-name-text">{name || fallback}</span>
        {canEdit && (
          <IconButton
            className="run-name-edit"
            icon={<IconPencil size={14} />}
            onClick={open}
            label={`Rename this ${label}`}
          />
        )}
      </span>
    );
  }

  return (
    <span className="run-name is-editing" onClick={(e) => e.stopPropagation()}>
      <span className="run-name-field">
        <input
          ref={inputRef}
          className="run-name-input"
          value={value}
          maxLength={MAX_LENGTH}
          placeholder={fallback}
          aria-label={`Name for this ${label}`}
          aria-invalid={error ? "true" : undefined}
          onChange={(e) => {
            setValue(e.target.value);
            if (error) setError(null);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              commit(e);
            } else if (e.key === "Escape") {
              e.preventDefault();
              cancel();
            }
          }}
        />
        {error && <span className="run-name-error">{error}</span>}
      </span>
      {/* The pencil, become a tick. Same place, same size — the control did not
          move, it changed what pressing it means. */}
      <IconButton
        className="run-name-edit"
        icon={<IconCheck size={14} />}
        loading={saving}
        onClick={commit}
        label="Save this name"
      />
      <IconButton
        className="run-name-edit"
        icon={<IconX size={14} />}
        onClick={cancel}
        label="Cancel renaming"
      />
    </span>
  );
}
