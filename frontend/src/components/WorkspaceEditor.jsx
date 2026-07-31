import React, { useState } from "react";
import {
  editedFiles,
  flattenLeaves,
  getAt,
  isRedacted,
  sameValue,
  setAt,
  skillOf,
} from "../workspace_util.js";
import { IconRefresh } from "./icons.jsx";

// Edit the agent's config and skill files for one call (§10.2 / §10.7).
//
// The editor is a working copy of the whole workspace: what the agent server
// gave us (`snapshot`) stays untouched so every field can be reverted to it, and
// `edit` is what the developer has done to it. Turning that into a request is
// the composer's job, not this component's.
//
// Three things this deliberately does NOT claim:
//   * That the override took effect. The platform cannot verify that — the
//     evidence is the text appearing in the trace's first system message, which
//     the span view renders. The hint below says exactly that.
//   * That the workspace is real. With WORKSPACE_IMPL=fake it is canned, which
//     the header says rather than letting someone edit a fake skill expecting
//     the real agent to have it.
//   * That a redacted field is absent. The agent server withholds its own API
//     keys, and the field is shown disabled rather than dropped: a field that
//     vanishes invites someone to re-add it by hand and shadow the real value.
//
// A workspace that cannot be read is an error with its reason, never a blank
// form — losing the starting point silently would have the developer retype a
// skill from memory and then test the wrong text.
export default function WorkspaceEditor({
  snapshot, edit, onChange, loading, error, onReload, fakeSeam,
}) {
  const [tab, setTab] = useState("config");
  const [openFile, setOpenFile] = useState(null);
  // JSON that does not parse yet is held here rather than pushed into the
  // working copy: half-typed text is not a value, and propagating it would put
  // a string where the agent expects a list.
  const [jsonDrafts, setJsonDrafts] = useState({});
  const [newPath, setNewPath] = useState("");

  if (error) {
    return (
      <div className="workspace-editor">
        <div className="hint error-text">
          Could not read the agent's workspace: {error}
          <button className="linkish" onClick={onReload}>
            <IconRefresh size={12} /> retry
          </button>
        </div>
      </div>
    );
  }
  if (!snapshot || !edit) {
    return (
      <div className="workspace-editor">
        <div className="hint">{loading ? "Reading the agent's workspace…" : ""}</div>
      </div>
    );
  }

  // A secret the agent server masked rather than removed would otherwise get an
  // editable box, and typing over the mask would send the mask itself as the
  // new key. Redacted paths are shown below, disabled, whichever way they came.
  const leaves = flattenLeaves(edit.config).filter(
    ({ path }) => !isRedacted(path, snapshot.redacted_paths)
  );
  const changedPaths = leaves
    .filter(({ path, value }) => !sameValue(getAt(snapshot.config, path), value))
    .map(({ path }) => path);
  const changedFiles = editedFiles(snapshot.skills, edit.skills);
  const files = Object.keys(edit.skills).sort();
  const active = openFile && openFile in edit.skills ? openFile : null;

  const setConfig = (path, value) =>
    onChange({ ...edit, config: setAt(edit.config, path, value) });
  const setFile = (path, content) =>
    onChange({ ...edit, skills: { ...edit.skills, [path]: content } });

  function removeFile(path) {
    const next = { ...edit.skills };
    delete next[path];
    onChange({ ...edit, skills: next });
    if (openFile === path) setOpenFile(null);
  }

  function restoreFile(path) {
    // Back to the agent's own text — or gone entirely, if the file is one the
    // developer added.
    if (path in snapshot.skills) setFile(path, snapshot.skills[path]);
    else removeFile(path);
  }

  function addFile() {
    const path = newPath.trim().replace(/^\/+/, "");
    if (!path || path in edit.skills) return;
    onChange({ ...edit, skills: { ...edit.skills, [path]: "" } });
    setOpenFile(path);
    setNewPath("");
  }

  function resetAll() {
    onChange({ config: snapshot.config, skills: snapshot.skills });
    setJsonDrafts({});
  }

  const dirty = changedPaths.length + changedFiles.length;

  return (
    <div className="workspace-editor">
      <div className="workspace-head">
        <div className="workspace-tabs">
          <button
            className={tab === "config" ? "active" : ""}
            onClick={() => setTab("config")}
          >
            Config
            {changedPaths.length > 0 && <span className="count">{changedPaths.length}</span>}
          </button>
          <button
            className={tab === "skills" ? "active" : ""}
            onClick={() => setTab("skills")}
          >
            Skill files
            {changedFiles.length > 0 && <span className="count">{changedFiles.length}</span>}
          </button>
        </div>
        <div className="grow" />
        {dirty > 0 && (
          <button className="linkish" onClick={resetAll}>
            reset everything
          </button>
        )}
        <button onClick={onReload} disabled={loading} title="Re-read the agent's workspace">
          <IconRefresh size={13} /> {loading ? "Reading…" : "Reload"}
        </button>
      </div>

      <div className="hint workspace-source">
        {fakeSeam ? (
          <>WORKSPACE_IMPL=fake — this is a canned workspace, not the agent's.</>
        ) : (
          <>
            From the agent server
            {snapshot.version ? (
              <>
                {" "}
                at version <code>{snapshot.version}</code>
              </>
            ) : (
              <> (which reports no version, so staleness cannot be checked)</>
            )}
            .
          </>
        )}{" "}
        Edits apply to the next question only — nothing is written back.
      </div>

      {tab === "config" && (
        <div className="config-grid">
          {leaves.map(({ path, key, parent, value }) => {
            const original = getAt(snapshot.config, path);
            const changed = !sameValue(original, value);
            return (
              <ConfigField
                key={path}
                name={key}
                parent={parent}
                value={value}
                changed={changed}
                draft={jsonDrafts[path]}
                onDraft={(text) => setJsonDrafts({ ...jsonDrafts, [path]: text })}
                onClearDraft={() => {
                  const next = { ...jsonDrafts };
                  delete next[path];
                  setJsonDrafts(next);
                }}
                onChange={(v) => setConfig(path, v)}
                onRevert={() => setConfig(path, original)}
              />
            );
          })}
          {snapshot.redacted_paths.map((path) => (
            <div className="field config-field" key={path}>
              <label>
                <strong>{path.split(".").pop()}</strong>
                <span className="muted">{path.split(".").slice(0, -1).join(".")}</span>
              </label>
              <input value="" disabled placeholder="hidden by the agent server" />
              <div className="hint">
                A secret. The agent uses its own value; it is never sent here and
                cannot be overridden.
              </div>
            </div>
          ))}
          {leaves.length === 0 && snapshot.redacted_paths.length === 0 && (
            <div className="hint">This agent reports no configuration.</div>
          )}
        </div>
      )}

      {tab === "skills" && (
        <div className="skills-pane">
          <div className="skill-files">
            {files.map((path, i) => {
              const group = skillOf(path);
              const newGroup = i === 0 || skillOf(files[i - 1]) !== group;
              return (
                <React.Fragment key={path}>
                  {newGroup && <div className="skill-group">{group || "/"}</div>}
                  <button
                    className={`skill-file${active === path ? " active" : ""}`}
                    onClick={() => setOpenFile(path)}
                  >
                    <span className="path">{path.slice(group ? group.length + 1 : 0)}</span>
                    {snapshot.skills[path] !== edit.skills[path] && (
                      <span className="badge">{path in snapshot.skills ? "edited" : "new"}</span>
                    )}
                  </button>
                </React.Fragment>
              );
            })}
            {Object.keys(snapshot.skills)
              .filter((p) => !(p in edit.skills))
              .sort()
              .map((path) => (
                <button
                  key={path}
                  className="skill-file deleted"
                  onClick={() => restoreFile(path)}
                  title="Restore this file"
                >
                  <span className="path">{path}</span>
                  <span className="badge">deleted</span>
                </button>
              ))}
            <div className="skill-add">
              <input
                value={newPath}
                placeholder="billing/references/new.md"
                onChange={(e) => setNewPath(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addFile()}
              />
              <button onClick={addFile} disabled={!newPath.trim()}>
                Add
              </button>
            </div>
            {files.length === 0 && Object.keys(snapshot.skills).length === 0 && (
              <div className="hint">This agent has no skill files.</div>
            )}
          </div>

          <div className="skill-body">
            {active ? (
              <div className="field">
                <label>
                  {active}
                  {snapshot.skills[active] !== edit.skills[active] && (
                    <span className="badge">edited</span>
                  )}
                  <button
                    className="linkish"
                    onClick={() => restoreFile(active)}
                    disabled={snapshot.skills[active] === edit.skills[active]}
                    title="Restore the text as the agent server has it"
                  >
                    <IconRefresh size={12} /> revert
                  </button>
                  <button
                    className="linkish"
                    onClick={() => removeFile(active)}
                    title="Run the next question without this file"
                  >
                    delete
                  </button>
                </label>
                <textarea
                  className="skill-text"
                  value={edit.skills[active]}
                  onChange={(e) => setFile(active, e.target.value)}
                />
              </div>
            ) : (
              <div className="hint">Pick a file to edit it.</div>
            )}
          </div>
        </div>
      )}

      {dirty > 0 && (
        <div className="hint">
          Sent with this one call as <code>metadata.workspace</code>. Whether the
          agent honoured it is visible in the trace — the text appears in the
          first span's system message.
        </div>
      )}
    </div>
  );
}

// One config value. The label carries the path, so a field named `model` says
// which `model` it is — the config is nested and several branches reuse names.
function ConfigField({
  name, parent, value, changed, draft, onDraft, onClearDraft, onChange, onRevert,
}) {
  const label = (
    <label>
      <strong>{name}</strong>
      {parent && <span className="muted">{parent}</span>}
      {changed && <span className="badge">edited</span>}
      {changed && (
        <button className="linkish" onClick={onRevert} title="Restore the agent's value">
          <IconRefresh size={12} /> revert
        </button>
      )}
    </label>
  );

  if (typeof value === "boolean") {
    return (
      <div className="field config-field">
        {label}
        <label className="checkline">
          <input
            type="checkbox"
            checked={value}
            onChange={(e) => onChange(e.target.checked)}
          />
          <span>{value ? "true" : "false"}</span>
        </label>
      </div>
    );
  }

  if (typeof value === "number") {
    return (
      <div className="field config-field">
        {label}
        <input
          type="number"
          value={value}
          onChange={(e) => {
            const n = Number(e.target.value);
            // A cleared or unparseable box keeps the raw text rather than
            // silently sending 0 — an accidental 0 is a real setting, and the
            // developer would never see that it happened.
            onChange(e.target.value === "" || !Number.isFinite(n) ? e.target.value : n);
          }}
        />
      </div>
    );
  }

  if (value !== null && typeof value === "object") {
    const text = draft !== undefined ? draft : JSON.stringify(value, null, 2);
    let invalid = false;
    if (draft !== undefined) {
      try {
        JSON.parse(draft);
      } catch {
        invalid = true;
      }
    }
    return (
      <div className="field config-field wide">
        {label}
        <textarea
          className="config-json"
          value={text}
          onChange={(e) => {
            onDraft(e.target.value);
            try {
              onChange(JSON.parse(e.target.value));
            } catch {
              // Keep the last valid value in the working copy; the draft above
              // is what the developer sees until it parses again.
            }
          }}
          onBlur={() => !invalid && onClearDraft()}
        />
        {invalid && <div className="hint error-text">Not valid JSON yet — not sent.</div>}
      </div>
    );
  }

  return (
    <div className="field config-field">
      {label}
      <input
        value={value === null ? "" : String(value)}
        placeholder={value === null ? "null" : ""}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}
