import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import Modal from "./Modal.jsx";
import ShareEditor from "./ShareEditor.jsx";
import { useToast } from "./Toast.jsx";
import { IconPlus } from "./icons.jsx";

const SAMPLE = `{"question": "How much did ACME owe at end of Q2?", "ground_truth_response": "ACME owed $42,180.", "ground_truth_reasoning_process_description": "Read billing skill, query invoices for ACME/Q2, sum balances.", "skill": ["billing"]}
{"question": "List overdue invoices for EMEA.", "ground_truth_response": "INV-1021, INV-1044, INV-1102.", "ground_truth_reasoning_process_description": "Read billing skill, query overdue+EMEA, list numbers.", "skill": ["billing"]}`;

// Upload (JSONL only for Stage 1). Owner can pick who to share with (§6.16).
// Existing metadata keys are auto-suggested (§6.10). Set is locked after creation.
export default function UploadDialog({ onClose, onCreated, users, subject }) {
  const toast = useToast();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [jsonl, setJsonl] = useState(SAMPLE);
  const [metaRows, setMetaRows] = useState([{ k: "", v: "" }]);
  const [shares, setShares] = useState([]);
  const [knownKeys, setKnownKeys] = useState([]);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.metadataKeys().then(setKnownKeys).catch(() => {});
  }, []);

  const setRow = (i, field, val) =>
    setMetaRows((rows) => rows.map((r, j) => (j === i ? { ...r, [field]: val } : r)));

  async function submit() {
    setError(null);
    if (!name.trim()) return setError("Name is required.");
    const metadata = {};
    metaRows.forEach((r) => { if (r.k.trim()) metadata[r.k.trim()] = r.v; });
    setBusy(true);
    try {
      await api.createEvalSet({ name, description, metadata, shares, jsonl });
      toast.success("Eval set created");
      onCreated();
    } catch (e) {
      const d = e.detail;
      if (d && d.upload_errors) setError("Upload errors:\n" + d.upload_errors.join("\n"));
      else setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title="Upload eval set"
      subtitle="JSONL, one question per line. The set is locked after creation (edit only)."
      onClose={onClose}
      width={620}
      footer={
        <>
          <button onClick={onClose}>Cancel</button>
          <button className="primary" disabled={busy} onClick={submit}>
            {busy ? "Uploading…" : "Create"}
          </button>
        </>
      }
    >
      {error && <div className="error" style={{ whiteSpace: "pre-wrap" }}>{error}</div>}
      <div className="field">
        <label>Name</label>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="My eval set" autoFocus />
      </div>
      <div className="field">
        <label>Description</label>
        <input value={description} onChange={(e) => setDescription(e.target.value)} />
      </div>

      <div className="field">
        <label>Share with</label>
        <ShareEditor shares={shares} setShares={setShares} knownUsers={users || []} currentUser={subject} />
      </div>

      <div className="field">
        <label>Custom metadata {knownKeys.length > 0 && <span className="hint">· known: {knownKeys.join(", ")}</span>}</label>
        {metaRows.map((r, i) => (
          <div key={i} style={{ display: "flex", gap: 8, marginBottom: 6 }}>
            <input list="known-keys" placeholder="key" value={r.k} onChange={(e) => setRow(i, "k", e.target.value)} />
            <input placeholder="value" value={r.v} onChange={(e) => setRow(i, "v", e.target.value)} />
          </div>
        ))}
        <datalist id="known-keys">{knownKeys.map((k) => <option key={k} value={k} />)}</datalist>
        <button onClick={() => setMetaRows((r) => [...r, { k: "", v: "" }])}><IconPlus size={14} /> add key</button>
      </div>

      <div className="field">
        <label>JSONL</label>
        <textarea value={jsonl} onChange={(e) => setJsonl(e.target.value)} />
      </div>
    </Modal>
  );
}
