import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import Modal from "./Modal.jsx";
import ShareEditor from "./ShareEditor.jsx";
import { useToast } from "./Toast.jsx";
import { IconPlus, IconUpload, IconX } from "./icons.jsx";
import {
  detectFormat,
  emptyRow,
  parseFile,
  rowsToJsonl,
  validateRows,
} from "../upload_parse.js";

// A couple of rows so the dialog is usable/demoable without a file on hand.
const SAMPLE_ROWS = [
  {
    question: "How much did ACME owe at end of Q2?",
    response: "ACME owed $42,180.",
    reasoning: "Read billing skill, query invoices for ACME/Q2, sum balances.",
    skill: "billing",
    question_id: "",
  },
  {
    question: "List overdue invoices for EMEA.",
    response: "INV-1021, INV-1044, INV-1102.",
    reasoning: "Read billing skill, query overdue+EMEA, list numbers.",
    skill: "billing",
    question_id: "",
  },
];

// Upload dialog: pick a JSONL or CSV file → preview it as an editable table →
// tweak rows → Create. Owner can pick who to share with (§6.16, direct name
// entry). Existing metadata keys are auto-suggested (§6.10). The set is locked
// after creation (§6.11), so all row add/remove happens here, pre-commit.
export default function UploadDialog({ onClose, onCreated, subject }) {
  const toast = useToast();
  const fileRef = useRef(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [rows, setRows] = useState([]);
  const [fileName, setFileName] = useState(null);
  const [parseErrors, setParseErrors] = useState([]);
  const [metaRows, setMetaRows] = useState([{ k: "", v: "" }]);
  const [shares, setShares] = useState([]);
  const [knownKeys, setKnownKeys] = useState([]);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.metadataKeys().then(setKnownKeys).catch(() => {});
  }, []);

  const setMeta = (i, field, val) =>
    setMetaRows((rs) => rs.map((r, j) => (j === i ? { ...r, [field]: val } : r)));

  const setCell = (i, field, val) =>
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, [field]: val } : r)));
  const removeRow = (i) => setRows((rs) => rs.filter((_, j) => j !== i));
  const addRow = () => setRows((rs) => [...rs, emptyRow()]);

  async function onFile(e) {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    setError(null);
    try {
      const text = await file.text();
      const { rows: parsed, errors } = parseFile(text, detectFormat(file.name));
      setRows(parsed);
      setParseErrors(errors);
      setFileName(file.name);
      if (parsed.length === 0 && errors.length === 0) {
        setParseErrors(["file contained no questions"]);
      }
    } catch (err) {
      setError("Could not read file: " + err.message);
    }
  }

  function loadSample() {
    setRows(SAMPLE_ROWS.map((r) => ({ ...r })));
    setParseErrors([]);
    setFileName("sample.jsonl");
    setError(null);
  }

  async function submit() {
    setError(null);
    if (!name.trim()) return setError("Name is required.");
    const rowErrors = validateRows(rows);
    if (rowErrors.length) return setError("Please fix:\n" + rowErrors.join("\n"));

    const metadata = {};
    metaRows.forEach((r) => { if (r.k.trim()) metadata[r.k.trim()] = r.v; });
    setBusy(true);
    try {
      await api.createEvalSet({ name, description, metadata, shares, jsonl: rowsToJsonl(rows) });
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
      subtitle="Upload a JSONL or CSV file, preview and edit the rows, then create. The set is locked after creation."
      onClose={onClose}
      width={960}
      footer={
        <>
          <button onClick={onClose}>Cancel</button>
          <button className="primary" disabled={busy} onClick={submit}>
            {busy ? "Uploading…" : `Create${rows.length ? ` (${rows.length})` : ""}`}
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
        <ShareEditor shares={shares} setShares={setShares} currentUser={subject} />
      </div>

      <div className="field">
        <label>Custom metadata {knownKeys.length > 0 && <span className="hint">· known: {knownKeys.join(", ")}</span>}</label>
        {metaRows.map((r, i) => (
          <div key={i} style={{ display: "flex", gap: 8, marginBottom: 6 }}>
            <input list="known-keys" placeholder="key" value={r.k} onChange={(e) => setMeta(i, "k", e.target.value)} />
            <input placeholder="value" value={r.v} onChange={(e) => setMeta(i, "v", e.target.value)} />
          </div>
        ))}
        <datalist id="known-keys">{knownKeys.map((k) => <option key={k} value={k} />)}</datalist>
        <button onClick={() => setMetaRows((r) => [...r, { k: "", v: "" }])}><IconPlus size={14} /> add key</button>
      </div>

      <div className="field">
        <label>Eval file (JSONL or CSV)</label>
        <div className="upload-picker">
          <input
            ref={fileRef}
            type="file"
            accept=".jsonl,.json,.csv,text/csv,application/json"
            style={{ display: "none" }}
            onChange={onFile}
          />
          <button onClick={() => fileRef.current && fileRef.current.click()}>
            <IconUpload size={14} /> Choose file…
          </button>
          <button className="link-btn" onClick={loadSample}>load sample</button>
          {fileName && <span className="hint">{fileName} · {rows.length} row{rows.length === 1 ? "" : "s"}</span>}
        </div>
        {parseErrors.length > 0 && (
          <div className="error" style={{ whiteSpace: "pre-wrap", marginTop: 8 }}>
            {"Parse warnings:\n" + parseErrors.join("\n")}
          </div>
        )}
      </div>

      <div className="field">
        <label>Preview {rows.length > 0 && <span className="hint">· edit any cell before creating</span>}</label>
        {rows.length === 0 ? (
          <div className="upload-empty">No rows yet — choose a JSONL/CSV file, load the sample, or add a row.</div>
        ) : (
          <div className="upload-table-wrap">
            <table className="upload-table">
              <thead>
                <tr>
                  <th className="rownum">#</th>
                  <th>question</th>
                  <th>ground_truth_response</th>
                  <th>reasoning_process_description</th>
                  <th className="skillcol">skill(s)</th>
                  <th className="qidcol">question_id</th>
                  <th aria-label="remove" />
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i}>
                    <td className="rownum">{i + 1}</td>
                    <td><textarea rows={2} value={r.question} onChange={(e) => setCell(i, "question", e.target.value)} /></td>
                    <td><textarea rows={2} value={r.response} onChange={(e) => setCell(i, "response", e.target.value)} /></td>
                    <td><textarea rows={2} value={r.reasoning} onChange={(e) => setCell(i, "reasoning", e.target.value)} /></td>
                    <td className="skillcol"><input placeholder="billing, reports" value={r.skill} onChange={(e) => setCell(i, "skill", e.target.value)} /></td>
                    <td className="qidcol"><input placeholder="auto" value={r.question_id} onChange={(e) => setCell(i, "question_id", e.target.value)} /></td>
                    <td>
                      <button className="icon-btn" onClick={() => removeRow(i)} aria-label="Remove row">
                        <IconX size={15} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <button style={{ marginTop: 8 }} onClick={addRow}><IconPlus size={14} /> add row</button>
      </div>
    </Modal>
  );
}
