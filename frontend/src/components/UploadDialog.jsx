import React, { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api.js";
import FormatHelp from "./FormatHelp.jsx";
import Modal from "./Modal.jsx";
import PreviewPager from "./PreviewPager.jsx";
import ScriptRunPanel from "./ScriptRunPanel.jsx";
import ShareEditor from "./ShareEditor.jsx";
import { useToast } from "./Toast.jsx";
import UploadPreviewEditor from "./UploadPreviewEditor.jsx";
import { IconPlus, IconUpload, IconX } from "./icons.jsx";
import Button from "./ui/Button.jsx";
import {
  clampPage,
  detectFormat,
  emptyRow,
  globalIndex,
  pageOfRow,
  pageSlice,
  parseFile,
  rowsFromScriptOutput,
  rowsToJsonl,
  validateRows,
} from "../upload_parse.js";
import Banner, { BannerDetail } from "./ui/Banner.jsx";

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

const EMPTY_CONNECTION = {
  host: "",
  port: "5432",
  database: "",
  user: "",
  password: "",
};

// Upload dialog: pick a JSONL, CSV or Python file, preview the rows as an
// editable table, tweak them, then Create. Owner can pick who to share with
// (direct name entry). Existing metadata keys are auto-suggested (§6.10). The set
// is locked after creation (§6.11), so all row add/remove happens here, pre-commit.
//
// **There is no source selector, and that is deliberate.** A `.py` is chosen with
// the same "Choose file…" button as a `.csv`, and the extension decides what
// happens next — so the developer who only ever uploads files sees exactly the
// dialog they saw before scripts existed, and the one uploading a script never
// had to find a mode switch. Everything script-specific lives in
// `ScriptRunPanel`, which does not exist in the tree unless a `.py` was chosen.
//
// A script's rows land in the same `rows` state as a file's, in the same shape,
// so the preview, the row editor, validation and Create are shared code rather
// than a second path that has to be kept in step with the first.
export default function UploadDialog({ onClose, onCreated, subject }) {
  const toast = useToast();
  const fileRef = useRef(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [rows, setRows] = useState([]);
  const [fileName, setFileName] = useState(null);
  // Which format the developer uploaded; recorded on the eval set for provenance
  // (the payload itself is always JSONL).
  const [sourceFormat, setSourceFormat] = useState("jsonl");
  const [parseErrors, setParseErrors] = useState([]);
  const [metaRows, setMetaRows] = useState([{ k: "", v: "" }]);
  const [shares, setShares] = useState([]);
  const [knownKeys, setKnownKeys] = useState([]);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  // The preview has two shapes: the parsed-file table, and a full-height
  // two-pane editor for actually rewriting rows. Same `rows` either way, so
  // toggling never costs an edit.
  const [expanded, setExpanded] = useState(false);

  // Paging over `rows`. Applies to every source: a JSONL file can be as long as
  // a script's output, and thousands of live <textarea>s are slow wherever they
  // came from.
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  // Script upload only. `null` throughout for a file upload, which is what keeps
  // the panel and the provenance payload out of the file path entirely.
  const [script, setScript] = useState(null); // { source, fileName }
  const [validation, setValidation] = useState(null);
  const [connection, setConnection] = useState(EMPTY_CONNECTION);
  const [runResult, setRunResult] = useState(null);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    api.metadataKeys().then(setKnownKeys).catch(() => {});
  }, []);

  const setMeta = (i, field, val) =>
    setMetaRows((rs) => rs.map((r, j) => (j === i ? { ...r, [field]: val } : r)));
  const removeMeta = (i) =>
    setMetaRows((rs) => (rs.length === 1 ? [{ k: "", v: "" }] : rs.filter((_, j) => j !== i)));
  // Clicking a known key fills the first empty key box rather than appending a
  // row, so repeat clicks don't leave a trail of blanks.
  const useKnownKey = (key) =>
    setMetaRows((rs) => {
      const i = rs.findIndex((r) => !r.k.trim());
      if (i < 0) return [...rs, { k: key, v: "" }];
      return rs.map((r, j) => (j === i ? { ...r, k: key } : r));
    });

  const setCell = (i, field, val) =>
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, [field]: val } : r)));
  const removeRow = (i) => setRows((rs) => rs.filter((_, j) => j !== i));
  const addRow = () => {
    // Land on the page the new row is actually on, otherwise "Add row" appears to
    // do nothing once the list is longer than one page. Both updates are computed
    // from `rows` here rather than inside the setRows updater — a state setter
    // called from another setter's updater runs twice under StrictMode.
    setPage(pageOfRow(rows.length, pageSize));
    setRows((rs) => [...rs, emptyRow()]);
  };

  const { items: pageRows, start: pageStart } = useMemo(
    () => pageSlice(rows, page, pageSize),
    [rows, page, pageSize]
  );

  // Deleting rows can strand the cursor past the end of the list.
  useEffect(() => {
    setPage((p) => clampPage(p, rows.length, pageSize));
  }, [rows.length, pageSize]);

  function resetSource() {
    setRows([]);
    setParseErrors([]);
    setScript(null);
    setValidation(null);
    setRunResult(null);
    setPage(1);
  }

  async function onFile(e) {
    const file = e.target.files && e.target.files[0];
    // Reset the input so choosing the same file twice re-fires change — the
    // normal thing to do after editing a script and trying again.
    e.target.value = "";
    if (!file) return;
    setError(null);
    resetSource();

    const format = detectFormat(file.name);
    setSourceFormat(format);
    setFileName(file.name);

    let text;
    try {
      text = await file.text();
    } catch (err) {
      setError("Could not read file: " + err.message);
      return;
    }

    if (format === "python") {
      setScript({ source: text, fileName: file.name });
      try {
        setValidation(await api.validateScript(text));
      } catch (err) {
        setError(err.message);
      }
      return;
    }

    const { rows: parsed, errors } = parseFile(text, format);
    setRows(parsed);
    setParseErrors(errors);
    if (parsed.length === 0 && errors.length === 0) {
      setParseErrors(["file contained no questions"]);
    }
  }

  async function runScript() {
    if (!script) return;
    setError(null);
    setRunning(true);
    try {
      const result = await api.runScript(script.source, {
        ...connection,
        port: Number(connection.port) || 5432,
      });
      setRunResult(result);
      setRows(rowsFromScriptOutput(result.rows));
      setPage(1);
      if (result.ok && result.rows.length) {
        toast.success(
          `${result.rows.length} row${result.rows.length === 1 ? "" : "s"} loaded`
        );
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setRunning(false);
    }
  }

  function loadSample() {
    resetSource();
    setRows(SAMPLE_ROWS.map((r) => ({ ...r })));
    setFileName("sample.jsonl");
    setSourceFormat("jsonl");
    setError(null);
  }

  async function submit() {
    setError(null);
    if (!name.trim()) return setError("Name is required.");
    const rowErrors = validateRows(rows);
    if (rowErrors.length) {
      // Jump to the first offending row: with a paginated preview, naming a row
      // the user cannot see is only half an error message.
      if (rowErrors[0].index >= 0) setPage(pageOfRow(rowErrors[0].index, pageSize));
      return setError(
        "Please fix:\n" + rowErrors.map((e) => e.message).join("\n")
      );
    }

    const metadata = {};
    metaRows.forEach((r) => { if (r.k.trim()) metadata[r.k.trim()] = r.v; });
    setBusy(true);
    try {
      await api.createEvalSet({
        name, description, metadata, shares,
        jsonl: rowsToJsonl(rows),
        source_format: sourceFormat,
        // Provenance for a script-built set: which script produced these rows and
        // which database it read. No password — the field does not exist on the
        // server's model, and a payload carrying one is refused.
        ...(sourceFormat === "python" && script
          ? {
              script: {
                source: script.source,
                db_host: connection.host,
                db_port: Number(connection.port) || 5432,
                db_name: connection.database,
                db_user: connection.user,
              },
            }
          : {}),
      });
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

  const isScript = sourceFormat === "python" && script;

  return (
    <Modal
      title="Upload eval set"
      subtitle={
        expanded
          ? "Editing rows. Collapse to get back to the rest of the form — nothing is lost."
          : "Upload a JSONL or CSV file, or a Python script that queries your database. Preview and edit the rows, then create. The set is locked after creation."
      }
      onClose={onClose}
      onDismiss={expanded ? () => setExpanded(false) : onClose}
      width={expanded ? "min(1200px, 96vw)" : 960}
      height={expanded ? "92vh" : undefined}
      footer={
        <>
          {expanded ? (
            <Button variant="ghost" onClick={() => setExpanded(false)}>Collapse</Button>
          ) : (
            <Button variant="ghost" onClick={onClose}>Cancel</Button>
          )}
          <Button variant="primary" loading={busy} onClick={submit}>
            {busy ? "Uploading…" : `Create${rows.length ? ` (${rows.length})` : ""}`}
          </Button>
        </>
      }
    >
      {error && (
        <Banner tone="error" title="Could not read that file">
          <BannerDetail>{error}</BannerDetail>
        </Banner>
      )}

      {!expanded && (
        <>
          <div className="field">
            <label>Name</label>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="My eval set" autoFocus />
          </div>
          <div className="field">
            <label>Description</label>
            <input value={description} onChange={(e) => setDescription(e.target.value)} />
          </div>

          <div className="field">
            <label>Custom metadata <span className="hint">· optional</span></label>
            <p className="hint meta-help">
              Labels for finding this set later — you can filter by them on the home page,
              and they show on the set&rsquo;s card. For example <code>team = billing</code> or{" "}
              <code>quarter = 2026Q3</code>.
            </p>
            {knownKeys.length > 0 && (
              <div className="meta-keys">
                <span className="hint">Already in use:</span>
                {knownKeys.map((k) => (
                  <button key={k} type="button" className="ui-chip-btn" onClick={() => useKnownKey(k)}>
                    {k}
                  </button>
                ))}
              </div>
            )}
            {metaRows.map((r, i) => (
              <div key={i} className="meta-row">
                <input list="known-keys" placeholder="team" value={r.k} onChange={(e) => setMeta(i, "k", e.target.value)} aria-label={`Metadata key ${i + 1}`} />
                <input placeholder="billing" value={r.v} onChange={(e) => setMeta(i, "v", e.target.value)} aria-label={`Metadata value ${i + 1}`} />
                <button
                  className="ui-btn ui-btn-ghost ui-btn-icon"
                  onClick={() => removeMeta(i)}
                  disabled={metaRows.length === 1 && !r.k && !r.v}
                  aria-label={`Remove metadata row ${i + 1}`}
                >
                  <IconX size={15} />
                </button>
              </div>
            ))}
            <datalist id="known-keys">{knownKeys.map((k) => <option key={k} value={k} />)}</datalist>
            <Button size="sm" icon={<IconPlus size={14} />} onClick={() => setMetaRows((r) => [...r, { k: "", v: "" }])}>Add label</Button>
          </div>

          <div className="field">
            <label>Share with</label>
            <ShareEditor shares={shares} setShares={setShares} currentUser={subject} />
          </div>
        </>
      )}

      {!expanded && (
      <div className="field">
        <label>Eval file <span className="hint">· JSONL, CSV, or a Python script</span></label>
        <div className="upload-picker">
          <input
            ref={fileRef}
            type="file"
            accept=".jsonl,.json,.csv,.py,text/csv,application/json,text/x-python"
            style={{ display: "none" }}
            onChange={onFile}
          />
          <Button
            icon={<IconUpload size={14} />}
            onClick={() => fileRef.current && fileRef.current.click()}
          >
            Choose file…
          </Button>
          {/* Before FormatHelp: that panel is full-width and wraps onto its own
              line when open, which would push "legacy.csv · 3 rows" below it and
              away from the button it describes. A script's filename is not shown
              here — it heads the checklist instead. */}
          {fileName && !isScript && (
            <span className="hint">{fileName} · {rows.length} row{rows.length === 1 ? "" : "s"}</span>
          )}
          <FormatHelp onLoadSample={loadSample} />
        </div>
        {parseErrors.length > 0 && (
          <Banner tone="warning" title="Some rows could not be read">
            <BannerDetail>{parseErrors.join("\n")}</BannerDetail>
          </Banner>
        )}
        {isScript && (
          <ScriptRunPanel
            fileName={script.fileName}
            validation={validation}
            connection={connection}
            setConnection={setConnection}
            onRun={runScript}
            running={running}
            result={runResult}
          />
        )}
      </div>
      )}

      <div className={`field${expanded ? " field-fill" : ""}`}>
        <div className="field-head">
          <label>Preview {!expanded && rows.length > 0 && <span className="hint">· edit any cell before creating</span>}</label>
          <span className="grow" />
          <Button variant="link" onClick={() => setExpanded((v) => !v)}>
            {expanded ? "Collapse" : "Expand"}
          </Button>
        </div>
        {expanded ? (
          <UploadPreviewEditor
            rows={rows}
            setCell={setCell}
            addRow={addRow}
            removeRow={removeRow}
            page={page}
            pageSize={pageSize}
            setPage={setPage}
            setPageSize={setPageSize}
          />
        ) : rows.length === 0 ? (
          <div className="upload-empty">
            {isScript
              ? "No rows yet — fill in the database connection above and run the script."
              : "No rows yet — choose a JSONL/CSV file, load the sample, or add a row."}
          </div>
        ) : (
          <>
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
                  {pageRows.map((r, i) => {
                    // Every write goes through the global index. Editing row 3 of
                    // page 2 must change row 23, not row 3 — see globalIndex and
                    // its tests in upload_parse.test.js.
                    const gi = globalIndex(page, pageSize, i);
                    return (
                      <tr key={gi}>
                        <td className="rownum">{gi + 1}</td>
                        <td><textarea rows={2} value={r.question} onChange={(e) => setCell(gi, "question", e.target.value)} /></td>
                        <td><textarea rows={2} value={r.response} onChange={(e) => setCell(gi, "response", e.target.value)} /></td>
                        <td><textarea rows={2} value={r.reasoning} onChange={(e) => setCell(gi, "reasoning", e.target.value)} /></td>
                        <td className="skillcol"><input placeholder="billing, reports" value={r.skill} onChange={(e) => setCell(gi, "skill", e.target.value)} /></td>
                        <td className="qidcol"><input placeholder="auto" value={r.question_id} onChange={(e) => setCell(gi, "question_id", e.target.value)} /></td>
                        <td>
                          <button className="ui-btn ui-btn-ghost ui-btn-icon" onClick={() => removeRow(gi)} aria-label={`Remove row ${gi + 1}`}>
                            <IconX size={15} />
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <PreviewPager
              total={rows.length}
              page={page}
              size={pageSize}
              onPage={setPage}
              onSize={setPageSize}
            />
          </>
        )}
        {!expanded && (
          <Button size="sm" style={{ marginTop: 8 }} icon={<IconPlus size={14} />} onClick={addRow}>Add row</Button>
        )}
      </div>
    </Modal>
  );
}
