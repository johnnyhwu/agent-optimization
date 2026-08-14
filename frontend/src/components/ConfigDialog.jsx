import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { initialConfigTab } from "../config_tab.js";
import Modal from "./Modal.jsx";
import ShareEditor from "./ShareEditor.jsx";
import JudgePromptEditor from "./JudgePromptEditor.jsx";
import { useToast } from "./Toast.jsx";
import { IconPlus, IconX } from "./icons.jsx";
import Button, { IconButton } from "./ui/Button.jsx";

// Owner-only set config (§6.10/§6.16): name, description, metadata keys, the
// share list, and how this set's answers are graded. Name/desc/metadata/judge
// prompt go through the versioned PATCH (409 flow); the share list goes through
// PUT /roles.
//
// Tabbed rather than one column, because the judge prompt is two large
// textareas: stacked under the metadata rows they would push the share list off
// the bottom of a laptop screen, and "scroll until you find it" is how settings
// stop being found at all.
const TABS = [
  ["general", "General"],
  ["sharing", "Sharing"],
  ["judging", "Judging"],
];

export default function ConfigDialog({ evalSet, subject, onClose, onSaved }) {
  const toast = useToast();
  // Which tab to land on is a property of the set, not of the button that was
  // pressed. There is no `initialTab` prop to override it with: there were two
  // callers, they passed two different things, and the developer met whichever
  // one their route happened to go through.
  const [tab, setTab] = useState(initialConfigTab(evalSet));
  const [name, setName] = useState(evalSet.name);
  const [description, setDescription] = useState(evalSet.description || "");
  const [metaRows, setMetaRows] = useState(
    Object.entries(evalSet.metadata || {}).map(([k, v]) => ({ k, v: String(v) }))
  );
  // The *effective* prompt — the server resolves the set's override against the
  // shipped default before sending it, so the textarea always has real text in
  // it rather than a blank box that means "something you can't see applies".
  const [system, setSystem] = useState(evalSet.judge_prompt?.system_prompt || "");
  const [user, setUser] = useState(evalSet.judge_prompt?.user_prompt || "");
  // Only for the Judging tab's two notes (fake seam, score threshold).
  const [impls, setImpls] = useState({});
  const [threshold, setThreshold] = useState(null);
  // Exclude the current user (always owner, shown locked inside ShareEditor).
  const [shares, setShares] = useState(
    (evalSet.roles || []).filter((r) => r.subject !== subject).map((r) => ({ ...r }))
  );
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .runConfigDefaults()
      .then((r) => {
        setImpls(r.impls || {});
        setThreshold(r.judge_score_threshold ?? null);
      })
      .catch(() => {});
  }, []);

  const setRow = (i, field, val) =>
    setMetaRows((rows) => rows.map((r, j) => (j === i ? { ...r, [field]: val } : r)));
  const removeRow = (i) => setMetaRows((rows) => rows.filter((_, j) => j !== i));

  async function save() {
    setError(null);
    setBusy(true);
    const metadata = {};
    metaRows.forEach((r) => { if (r.k.trim()) metadata[r.k.trim()] = r.v; });
    try {
      // 1) versioned fields. The judge prompt goes in the same PATCH so an edit
      //    to the grading criteria gets the same 409 conflict protection the
      //    rest of the card has — two owners editing at once is the case that
      //    would otherwise silently lose one of them.
      await api.updateEvalSet(evalSet.id, {
        name, description, metadata,
        judge_system_prompt: system, judge_user_prompt: user,
        version: evalSet.version,
      });
      // 2) share list
      await api.updateRoles(evalSet.id, shares);
      toast.success("Saved");
      onSaved();
    } catch (e) {
      if (e.status === 409) {
        setError("This card was modified by someone else. Reload and retry.");
        toast.error("Conflict — reload and retry");
      } else {
        setError(e.message);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title="Configure eval set"
      subtitle={`${evalSet.name} · v${evalSet.version}`}
      onClose={onClose}
      width={680}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" loading={busy} onClick={save}>
            {busy ? "Saving…" : "Save changes"}
          </Button>
        </>
      }
    >
      {error && <div className="error">{error}</div>}

      <div className="ui-segmented" style={{ marginBottom: 14 }} role="tablist">
        {TABS.map(([id, label]) => (
          <button key={id} type="button" role="tab" aria-selected={tab === id} className={tab === id ? "is-active" : ""} onClick={() => setTab(id)}>
            {label}
            {/* Unreviewed grading criteria are the one thing here worth a nudge:
                a brand-new set grades with a prompt nobody has looked at. */}
            {id === "judging" && !evalSet.judge_prompt?.reviewed_at && " !"}
          </button>
        ))}
      </div>

      {tab === "general" && (
        <>
          <div className="field">
            <label>Name</label>
            <input value={name} onChange={(e) => setName(e.target.value)} autoFocus />
          </div>
          <div className="field">
            <label>Description</label>
            <input value={description} onChange={(e) => setDescription(e.target.value)} />
          </div>
          <div className="field">
            <label>Metadata</label>
            {metaRows.length === 0 && <div className="hint" style={{ marginBottom: 6 }}>No metadata keys.</div>}
            {metaRows.map((r, i) => (
              <div key={i} style={{ display: "flex", gap: 8, marginBottom: 6 }}>
                <input placeholder="key" value={r.k} onChange={(e) => setRow(i, "k", e.target.value)} />
                <input placeholder="value" value={r.v} onChange={(e) => setRow(i, "v", e.target.value)} />
                <IconButton label="Remove" icon={<IconX size={15} />} onClick={() => removeRow(i)} />
              </div>
            ))}
            <Button size="sm" icon={<IconPlus size={14} />} onClick={() => setMetaRows((r) => [...r, { k: "", v: "" }])}>Add label</Button>
          </div>
        </>
      )}

      {tab === "sharing" && (
        <div className="field">
          <label>Share list</label>
          <ShareEditor shares={shares} setShares={setShares} currentUser={subject} />
        </div>
      )}

      {tab === "judging" && (
        <JudgePromptEditor
          evalSet={evalSet}
          system={system}
          setSystem={setSystem}
          user={user}
          setUser={setUser}
          impls={impls}
          threshold={threshold}
        />
      )}
    </Modal>
  );
}
