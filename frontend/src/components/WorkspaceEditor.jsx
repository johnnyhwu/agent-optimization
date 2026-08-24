import React, { useState } from "react";
import { editedFiles, skillOf } from "../workspace_util.js";
import { IconChevronRight, IconPlus, IconRefresh, IconTrash } from "./icons.jsx";

// Edit the agent's skill files for one call.
//
// The editor is a working copy of the whole skill directory: what the agent
// server gave us (`snapshot`) stays untouched so every file can be reverted to
// it, and `edit` is what the developer has done to it. Turning that into a
// request is the composer's job, not this component's; whether this panel is
// showing is the composer's too, so all of the composer's panels behave alike.
//
// **A file set, not a patch.** What travels replaces the agent's directory for
// that one call, which is why deleting a file is an edit this editor can
// express at all — "does it still answer without this reference?" is a
// legitimate experiment, and a patch format could not ask it.
//
// Two things this deliberately does NOT claim:
//   * That the override took effect. For a playground attempt the platform does
//     not verify that — the evidence is the text appearing in the trace's first
//     system message, which the span view renders, and the hint below says so.
//     (An optimization run does check, because there it decides whether an
//     hour of rollouts means anything; see the optimizer's pre-flight.)
//   * That the workspace is real. With WORKSPACE_IMPL=fake it is canned, which
//     the header says rather than letting someone edit a fake skill expecting
//     the real agent to have it.
//
// A workspace that cannot be read is an error with its reason, never a blank
// form — losing the starting point silently would have the developer retype a
// skill from memory and then test the wrong text.

export default function WorkspaceEditor({
  snapshot, edit, onChange, loading, error, onReload, fakeSeam,
}) {
  const [openFile, setOpenFile] = useState(null);
  const [newPath, setNewPath] = useState("");

  if (error) {
    return (
      <div className="workspace-editor">
        <div className="hint error-text">
          Could not read the agent's workspace: {error}{" "}
          <button className="ui-btn ui-btn-link" onClick={onReload}>
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

  const changedFiles = editedFiles(snapshot.skills, edit.skills);
  const files = Object.keys(edit.skills).sort();
  const deletedFiles = Object.keys(snapshot.skills).filter((p) => !(p in edit.skills)).sort();
  const active = openFile && openFile in edit.skills ? openFile : null;

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
    onChange({ skills: snapshot.skills });
  }

  const dirty = changedFiles.length;

  return (
    <div className="workspace-editor">
      <div className="workspace-bar">
        <span className="workspace-source">
          {fakeSeam ? (
            <>
              <strong>Demo mode</strong> — a canned workspace, not a real agent's.
            </>
          ) : snapshot.version ? (
            <>
              From the agent server at <code>{snapshot.version}</code>.
            </>
          ) : (
            <>From the agent server, which reports no version — staleness cannot be checked.</>
          )}{" "}
          Applies to the next question only; nothing is written back.
        </span>
        <div className="grow" />
        {dirty > 0 && (
          <button className="ui-btn ui-btn-link" onClick={resetAll}>
            Reset all {dirty}
          </button>
        )}
        <button
          className="ui-btn ui-btn-ghost ui-btn-icon"
          onClick={onReload}
          disabled={loading}
          title="Re-read the agent's skill files"
          aria-label="Reload the workspace"
        >
          <IconRefresh size={14} />
        </button>
      </div>

      <div className="skills-pane">
          <div className="skill-files">
            <div className="skill-scroll">
              {files.map((path, i) => {
                const group = skillOf(path);
                const newGroup = i === 0 || skillOf(files[i - 1]) !== group;
                const state =
                  snapshot.skills[path] === edit.skills[path]
                    ? null
                    : path in snapshot.skills
                      ? "edited"
                      : "new";
                return (
                  <React.Fragment key={path}>
                    {newGroup && <div className="skill-group">{group || "/"}</div>}
                    <button
                      className={`skill-file${active === path ? " active" : ""}`}
                      onClick={() => setOpenFile(path)}
                      title={path}
                    >
                      <span className="path">{path.slice(group ? group.length + 1 : 0)}</span>
                      {state && <span className="ui-badge ui-badge-warning">{state}</span>}
                    </button>
                  </React.Fragment>
                );
              })}
              {deletedFiles.length > 0 && <div className="skill-group">Deleted for this call</div>}
              {deletedFiles.map((path) => (
                <button
                  key={path}
                  className="skill-file deleted"
                  onClick={() => restoreFile(path)}
                  title={`Restore ${path}`}
                >
                  <span className="path">{path}</span>
                  <IconRefresh size={12} />
                </button>
              ))}
              {files.length === 0 && deletedFiles.length === 0 && (
                <div className="hint">
                  This agent has no skill files. That is a workable state — it
                  answers from its own prompt — and you can still add a file
                  below to try one out on the next question.
                </div>
              )}
            </div>

            <div className="skill-add">
              <label>New file</label>
              <div className="skill-add-row">
                <input
                  value={newPath}
                  placeholder="billing/references/new.md"
                  onChange={(e) => setNewPath(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && addFile()}
                />
                <button onClick={addFile} disabled={!newPath.trim()} title="Add this file">
                  <IconPlus size={13} />
                </button>
              </div>
            </div>
          </div>

          <div className="skill-body">
            {active ? (
              <>
                <div className="skill-body-head">
                  <span className="skill-path">{active}</span>
                  {snapshot.skills[active] !== edit.skills[active] && (
                    <span className="ui-badge ui-badge-warning">
                      {active in snapshot.skills ? "edited" : "new"}
                    </span>
                  )}
                  <div className="grow" />
                  <button
                    className="ui-btn ui-btn-ghost ui-btn-icon"
                    onClick={() => restoreFile(active)}
                    disabled={snapshot.skills[active] === edit.skills[active]}
                    title="Restore the text as the agent server has it"
                    aria-label="Revert this file"
                  >
                    <IconRefresh size={14} />
                  </button>
                  <button
                    className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-destructive-hover"
                    onClick={() => removeFile(active)}
                    title="Run the next question without this file"
                    aria-label="Delete this file for the next call"
                  >
                    <IconTrash size={14} />
                  </button>
                </div>
                <textarea
                  className="skill-text"
                  value={edit.skills[active]}
                  onChange={(e) => setFile(active, e.target.value)}
                />
              </>
            ) : (
              <div className="hint">Pick a file to edit it.</div>
            )}
          </div>
      </div>

      {dirty > 0 && (
        <div className="hint workspace-foot">
          Sent with this one call as <code>metadata.skills</code>, replacing the
          agent's directory for it. Whether the agent honoured that is visible in
          the trace — the text appears in the first span's system message.
        </div>
      )}
    </div>
  );
}
