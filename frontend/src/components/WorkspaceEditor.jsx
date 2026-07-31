import React, { useState } from "react";
import {
  editedFiles,
  flattenLeaves,
  getAt,
  isLeaf,
  isRedacted,
  sameValue,
  setAt,
  skillOf,
} from "../workspace_util.js";
import { IconChevronRight, IconPlus, IconRefresh, IconTrash } from "./icons.jsx";

// Edit the agent's config and skill files for one call (§10.2 / §10.7).
//
// The editor is a working copy of the whole workspace: what the agent server
// gave us (`snapshot`) stays untouched so every field can be reverted to it, and
// `edit` is what the developer has done to it. Turning that into a request is
// the composer's job, not this component's; which of the two panels is showing
// is the composer's too, so all four of the composer's panels behave alike.
//
// **The config is a tree, not a form.** Flattening it produced a wall of boxes
// where the only thing telling `enabled` under `tools.sql_query` apart from
// `enabled` under `tools.vector_search` was small grey text beside the name. The
// hierarchy is how the developer already thinks about config.json, so it is the
// structure here too: collapsed groups, an edit count on each, and one value per
// row so the controls line up down a single edge.
//
// Three things this deliberately does NOT claim:
//   * That the override took effect. The platform cannot verify that — the
//     evidence is the text appearing in the trace's first system message, which
//     the span view renders. The hint below says exactly that.
//   * That the workspace is real. With WORKSPACE_IMPL=fake it is canned, which
//     the header says rather than letting someone edit a fake skill expecting
//     the real agent to have it.
//   * That a redacted field is absent. The agent server withholds its own API
//     keys, and the field is shown in place, disabled, rather than dropped: a
//     field that vanishes invites someone to re-add it and shadow the real value.
//
// A workspace that cannot be read is an error with its reason, never a blank
// form — losing the starting point silently would have the developer retype a
// skill from memory and then test the wrong text.

// Marks a config leaf the agent server withheld. A symbol rather than a string
// so no real config value can ever be mistaken for one.
const REDACTED = Symbol("redacted");

export default function WorkspaceEditor({
  tab, snapshot, edit, onChange, loading, error, onReload, fakeSeam,
}) {
  const [openFile, setOpenFile] = useState(null);
  const [openGroups, setOpenGroups] = useState({});
  // JSON that does not parse yet is held here rather than pushed into the
  // working copy: half-typed text is not a value, and propagating it would put
  // a string where the agent expects a list.
  const [jsonDrafts, setJsonDrafts] = useState({});
  const [newPath, setNewPath] = useState("");

  if (error) {
    return (
      <div className="workspace-editor">
        <div className="hint error-text">
          Could not read the agent's workspace: {error}{" "}
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

  const changed = new Set(
    flattenLeaves(edit.config)
      .filter(({ path, value }) => !sameValue(getAt(snapshot.config, path), value))
      .map(({ path }) => path)
  );
  const changedFiles = editedFiles(snapshot.skills, edit.skills);
  const files = Object.keys(edit.skills).sort();
  const deletedFiles = Object.keys(snapshot.skills).filter((p) => !(p in edit.skills)).sort();
  const active = openFile && openFile in edit.skills ? openFile : null;

  // The tree the config panel renders: the working copy with each withheld
  // secret put back as a placeholder, so `api_key` appears under
  // `agents.defaults` where it lives rather than in a list of orphans.
  const tree = snapshot.redacted_paths.reduce(
    (acc, path) => (getAt(acc, path) === undefined ? setAt(acc, path, REDACTED) : acc),
    edit.config
  );

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

  const dirty = changed.size + changedFiles.length;

  return (
    <div className="workspace-editor">
      <div className="workspace-bar">
        <span className="workspace-source">
          {fakeSeam ? (
            <>
              <strong>WORKSPACE_IMPL=fake</strong> — a canned workspace, not the agent's.
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
          <button className="linkish" onClick={resetAll}>
            Reset all {dirty}
          </button>
        )}
        <button
          className="icon-btn"
          onClick={onReload}
          disabled={loading}
          title="Re-read the agent's config and skill files"
          aria-label="Reload the workspace"
        >
          <IconRefresh size={14} />
        </button>
      </div>

      {tab === "config" &&
        (Object.keys(tree).length === 0 ? (
          <div className="hint">This agent reports no configuration.</div>
        ) : (
          <div className="ws-tree">
            <ConfigNodes
              node={tree}
              prefix=""
              snapshot={snapshot}
              changed={changed}
              openGroups={openGroups}
              setOpenGroups={setOpenGroups}
              jsonDrafts={jsonDrafts}
              setJsonDrafts={setJsonDrafts}
              onSet={setConfig}
            />
          </div>
        ))}

      {tab === "skills" && (
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
                      {state && <span className="badge">{state}</span>}
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
                <div className="hint">This agent has no skill files.</div>
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
                    <span className="badge">
                      {active in snapshot.skills ? "edited" : "new"}
                    </span>
                  )}
                  <div className="grow" />
                  <button
                    className="icon-btn"
                    onClick={() => restoreFile(active)}
                    disabled={snapshot.skills[active] === edit.skills[active]}
                    title="Restore the text as the agent server has it"
                    aria-label="Revert this file"
                  >
                    <IconRefresh size={14} />
                  </button>
                  <button
                    className="icon-btn danger-btn"
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
      )}

      {dirty > 0 && (
        <div className="hint workspace-foot">
          Sent with this one call as <code>metadata.workspace</code>. Whether the
          agent honoured it is visible in the trace — the text appears in the
          first span's system message.
        </div>
      )}
    </div>
  );
}

// One level of the config tree: leaves first, then nested groups. Leaves before
// groups because a leaf is one line and a group is a whole section — reading a
// level's own values shouldn't mean scrolling past its children.
function ConfigNodes({ node, prefix, ...rest }) {
  const entries = Object.entries(node);
  const leaves = entries.filter(([, v]) => v === REDACTED || isLeaf(v));
  const groups = entries.filter(([, v]) => v !== REDACTED && !isLeaf(v));

  return (
    <>
      {leaves.map(([key, value]) => (
        <ConfigRow key={key} name={key} path={prefix + key} value={value} {...rest} />
      ))}
      {groups.map(([key, value]) => (
        <ConfigGroup key={key} name={key} path={prefix + key} node={value} {...rest} />
      ))}
    </>
  );
}

function ConfigGroup({ name, path, node, ...rest }) {
  const { changed, openGroups, setOpenGroups } = rest;
  const leaves = flattenLeaves(node);
  const editedHere = leaves.filter(({ path: p }) => changed.has(`${path}.${p}`)).length;
  // Collapsed by default — but a group holding an edit opens itself, so an
  // override carried in by a clone is never hidden behind a closed triangle.
  const open = openGroups[path] ?? editedHere > 0;

  return (
    <div className={`ws-node${open ? " open" : ""}`}>
      <button
        className="ws-node-head"
        onClick={() => setOpenGroups({ ...openGroups, [path]: !open })}
        aria-expanded={open}
      >
        <IconChevronRight size={12} />
        <span className="ws-node-name">{name}</span>
        <span className="ws-node-meta">
          {leaves.length} {leaves.length === 1 ? "value" : "values"}
        </span>
        {editedHere > 0 && <span className="badge">{editedHere} edited</span>}
      </button>
      {open && (
        <div className="ws-children">
          <ConfigNodes node={node} prefix={`${path}.`} {...rest} />
        </div>
      )}
    </div>
  );
}

// One config value: name on the left, control on the right, so the controls line
// up down a single edge whatever the key lengths are. The full path is the
// title — inside the tree the parent names are already on screen.
function ConfigRow({
  name, path, value, snapshot, changed, jsonDrafts, setJsonDrafts, onSet,
}) {
  if (value === REDACTED || isRedacted(path, snapshot.redacted_paths)) {
    return (
      <div className="ws-field" title={path}>
        <span className="ws-field-key">{name}</span>
        <div className="ws-field-control">
          <input value="" disabled placeholder="hidden by the agent server" />
        </div>
        <span className="ws-field-note">secret — the agent uses its own</span>
      </div>
    );
  }

  const original = getAt(snapshot.config, path);
  const isChanged = changed.has(path);
  const draft = jsonDrafts[path];
  const setDraft = (text) => setJsonDrafts({ ...jsonDrafts, [path]: text });
  const clearDraft = () => {
    const next = { ...jsonDrafts };
    delete next[path];
    setJsonDrafts(next);
  };

  let control;
  let wide = false;
  if (typeof value === "boolean") {
    control = (
      <label className="checkline">
        <input type="checkbox" checked={value} onChange={(e) => onSet(path, e.target.checked)} />
        <span>{value ? "true" : "false"}</span>
      </label>
    );
  } else if (typeof value === "number") {
    control = (
      <input
        type="number"
        value={value}
        onChange={(e) => {
          const n = Number(e.target.value);
          // A cleared or unparseable box keeps the raw text rather than silently
          // sending 0 — an accidental 0 is a real setting, and the developer
          // would never see that it happened.
          onSet(path, e.target.value === "" || !Number.isFinite(n) ? e.target.value : n);
        }}
      />
    );
  } else if (value !== null && typeof value === "object") {
    wide = true;
    const text = draft !== undefined ? draft : JSON.stringify(value, null, 2);
    let invalid = false;
    if (draft !== undefined) {
      try {
        JSON.parse(draft);
      } catch {
        invalid = true;
      }
    }
    control = (
      <>
        <textarea
          className="config-json"
          value={text}
          onChange={(e) => {
            setDraft(e.target.value);
            try {
              onSet(path, JSON.parse(e.target.value));
            } catch {
              // Keep the last valid value in the working copy; the draft above
              // is what the developer sees until it parses again.
            }
          }}
          onBlur={() => !invalid && clearDraft()}
        />
        {invalid && <div className="hint error-text">Not valid JSON yet — not sent.</div>}
      </>
    );
  } else {
    control = (
      <input
        value={value === null ? "" : String(value)}
        placeholder={value === null ? "null" : ""}
        onChange={(e) => onSet(path, e.target.value)}
      />
    );
  }

  return (
    <div className={`ws-field${wide ? " wide" : ""}${isChanged ? " changed" : ""}`} title={path}>
      <span className="ws-field-key">{name}</span>
      <div className="ws-field-control">{control}</div>
      {isChanged ? (
        <button
          className="icon-btn"
          onClick={() => {
            onSet(path, original);
            clearDraft();
          }}
          title={`Restore the agent's value: ${JSON.stringify(original)}`}
          aria-label={`Revert ${name}`}
        >
          <IconRefresh size={13} />
        </button>
      ) : (
        <span className="ws-field-spacer" />
      )}
    </div>
  );
}
