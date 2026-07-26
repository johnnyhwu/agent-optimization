import React, { useEffect, useState } from "react";
import { api } from "../api.js";

const SAMPLE = `{"question": "How much did ACME owe at end of Q2?", "ground_truth_response": "ACME owed $42,180.", "ground_truth_reasoning_process_description": "Read billing skill, query invoices for ACME/Q2, sum balances.", "skill": ["billing"]}
{"question": "List overdue invoices for EMEA.", "ground_truth_response": "INV-1021, INV-1044, INV-1102.", "ground_truth_reasoning_process_description": "Read billing skill, query overdue+EMEA, list numbers.", "skill": ["billing"]}`;

// Upload (JSONL only for Stage 1). Existing metadata keys are auto-suggested
// (§6.10). The set is locked after creation (no add/delete of questions).
export default function UploadDialog({ onClose, onCreated }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [jsonl, setJsonl] = useState(SAMPLE);
  const [metaRows, setMetaRows] = useState([{ k: "", v: "" }]);
  const [knownKeys, setKnownKeys] = useState([]);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.metadataKeys().then(setKnownKeys).catch(() => {});
  }, []);

  function setRow(i, field, val) {
    setMetaRows((rows) => rows.map((r, j) => (j === i ? { ...r, [field]: val } : r)));
  }

  async function submit() {
    setError(null);
    if (!name.trim()) return setError("Name is required.");
    const metadata = {};
    metaRows.forEach((r) => {
      if (r.k.trim()) metadata[r.k.trim()] = r.v;
    });
    setBusy(true);
    try {
      await api.createEvalSet({ name, description, metadata, jsonl });
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
    <div className="overlay" onClick={onClose}>
      <div className="dialog" onClick={(e) => e.stopPropagation()}>
        <h3>Upload eval set (JSONL)</h3>
        {error && <div className="error" style={{ whiteSpace: "pre-wrap" }}>{error}</div>}
        <div className="row">
          <div className="label muted">Name</div>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="My eval set" />
        </div>
        <div className="row">
          <div className="label muted">Description</div>
          <input value={description} onChange={(e) => setDescription(e.target.value)} />
        </div>
        <div className="row">
          <div className="label muted">
            Custom metadata {knownKeys.length > 0 && <span>(known: {knownKeys.join(", ")})</span>}
          </div>
          {metaRows.map((r, i) => (
            <div key={i} style={{ display: "flex", gap: 8, marginBottom: 6 }}>
              <input list="known-keys" placeholder="key" value={r.k} onChange={(e) => setRow(i, "k", e.target.value)} />
              <input placeholder="value" value={r.v} onChange={(e) => setRow(i, "v", e.target.value)} />
            </div>
          ))}
          <datalist id="known-keys">
            {knownKeys.map((k) => (
              <option key={k} value={k} />
            ))}
          </datalist>
          <button onClick={() => setMetaRows((r) => [...r, { k: "", v: "" }])}>+ add key</button>
        </div>
        <div className="row">
          <div className="label muted">JSONL (one question per line)</div>
          <textarea value={jsonl} onChange={(e) => setJsonl(e.target.value)} />
        </div>
        <div className="actions">
          <button onClick={onClose}>Cancel</button>
          <button className="primary" disabled={busy} onClick={submit}>
            {busy ? "Uploading…" : "Create"}
          </button>
        </div>
      </div>
    </div>
  );
}
