import React, { useState } from "react";
import { api } from "../api.js";
import Modal from "./Modal.jsx";
import ShareEditor from "./ShareEditor.jsx";
import { useToast } from "./Toast.jsx";
import { IconPlus, IconX } from "./icons.jsx";

// Owner-only card config (§6.10/§6.16): edit name, description, metadata keys, and
// the share list. Name/desc/metadata go through the versioned PATCH (409 flow);
// the share list goes through PUT /roles.
export default function ConfigDialog({ evalSet, users, subject, onClose, onSaved }) {
  const toast = useToast();
  const [name, setName] = useState(evalSet.name);
  const [description, setDescription] = useState(evalSet.description || "");
  const [metaRows, setMetaRows] = useState(
    Object.entries(evalSet.metadata || {}).map(([k, v]) => ({ k, v: String(v) }))
  );
  // Exclude the current user (always owner, shown locked inside ShareEditor).
  const [shares, setShares] = useState(
    (evalSet.roles || []).filter((r) => r.subject !== subject).map((r) => ({ ...r }))
  );
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const setRow = (i, field, val) =>
    setMetaRows((rows) => rows.map((r, j) => (j === i ? { ...r, [field]: val } : r)));
  const removeRow = (i) => setMetaRows((rows) => rows.filter((_, j) => j !== i));

  async function save() {
    setError(null);
    setBusy(true);
    const metadata = {};
    metaRows.forEach((r) => { if (r.k.trim()) metadata[r.k.trim()] = r.v; });
    try {
      // 1) versioned fields
      await api.updateEvalSet(evalSet.id, {
        name, description, metadata, version: evalSet.version,
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
      width={620}
      footer={
        <>
          <button onClick={onClose}>Cancel</button>
          <button className="primary" disabled={busy} onClick={save}>
            {busy ? "Saving…" : "Save changes"}
          </button>
        </>
      }
    >
      {error && <div className="error">{error}</div>}
      <div className="field">
        <label>Name</label>
        <input value={name} onChange={(e) => setName(e.target.value)} autoFocus />
      </div>
      <div className="field">
        <label>Description</label>
        <input value={description} onChange={(e) => setDescription(e.target.value)} />
      </div>

      <div className="field">
        <label>Share list</label>
        <ShareEditor shares={shares} setShares={setShares} knownUsers={users || []} currentUser={subject} />
      </div>

      <div className="field">
        <label>Metadata</label>
        {metaRows.length === 0 && <div className="hint" style={{ marginBottom: 6 }}>No metadata keys.</div>}
        {metaRows.map((r, i) => (
          <div key={i} style={{ display: "flex", gap: 8, marginBottom: 6 }}>
            <input placeholder="key" value={r.k} onChange={(e) => setRow(i, "k", e.target.value)} />
            <input placeholder="value" value={r.v} onChange={(e) => setRow(i, "v", e.target.value)} />
            <button className="icon-btn" onClick={() => removeRow(i)} aria-label="Remove"><IconX size={15} /></button>
          </div>
        ))}
        <button onClick={() => setMetaRows((r) => [...r, { k: "", v: "" }])}><IconPlus size={14} /> add key</button>
      </div>
    </Modal>
  );
}
