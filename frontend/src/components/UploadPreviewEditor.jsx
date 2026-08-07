import React, { useEffect, useRef, useState } from "react";
import { IconPlus, IconTrash } from "./icons.jsx";
import { rowMissing } from "../upload_parse.js";

// The upload preview, expanded. Same rows, same edits — a different shape for a
// different job: the table is for scanning what parsed, this is for rewriting a
// question you're not happy with. Row list on the left (incomplete rows marked,
// since fixing them is why you came here), full-height fields on the right.
// Mirrors QuestionEditor's two-pane layout so per-question editing looks the
// same wherever you meet it.
export default function UploadPreviewEditor({ rows, setCell, addRow, removeRow }) {
  const [active, setActive] = useState(0);
  const listRef = useRef(null);

  // Rows come and go while this is open; keep the cursor on a real row.
  const idx = Math.min(active, Math.max(rows.length - 1, 0));
  const row = rows[idx];

  const move = (delta) => {
    if (rows.length === 0) return;
    setActive((i) => Math.min(Math.max(i + delta, 0), rows.length - 1));
  };

  // ⌘/Ctrl+Enter and ⌘/Ctrl+↑/↓ step through rows without leaving the field
  // you're typing in. Bare arrows are left alone — they move the caret.
  function onKeyDown(e) {
    if (!(e.metaKey || e.ctrlKey)) return;
    if (e.key === "Enter" || e.key === "ArrowDown") { e.preventDefault(); move(1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
  }

  useEffect(() => {
    const el = listRef.current && listRef.current.children[idx];
    if (el && el.scrollIntoView) el.scrollIntoView({ block: "nearest" });
  }, [idx]);

  function onRemove() {
    removeRow(idx);
    setActive((i) => Math.max(Math.min(i, rows.length - 2), 0));
  }

  const missing = row ? rowMissing(row) : [];

  return (
    <div className="preview-editor" onKeyDown={onKeyDown}>
      <div className="preview-list">
        <div className="preview-list-rows" ref={listRef}>
          {rows.map((r, i) => {
            const gaps = rowMissing(r);
            return (
              <button
                key={i}
                type="button"
                className={`preview-item${i === idx ? " active" : ""}${gaps.length ? " incomplete" : ""}`}
                onClick={() => setActive(i)}
                aria-current={i === idx}
              >
                <span className="n">{i + 1}</span>
                <span className="grow">
                  <span className="qtext">{r.question.trim() || "Untitled question"}</span>
                  {gaps.length > 0 && (
                    <span className="gaps">missing {gaps.length === 1 ? shortName(gaps[0]) : `${gaps.length} fields`}</span>
                  )}
                </span>
              </button>
            );
          })}
        </div>
        <button className="preview-add ui-btn ui-btn-secondary" onClick={() => { addRow(); setActive(rows.length); }}>
          <IconPlus size={14} /> add row
        </button>
      </div>

      <div className="preview-pane">
        {!row ? (
          <div className="upload-empty">No rows yet — add one, or collapse and choose a file.</div>
        ) : (
          <>
            <div className="preview-pane-head">
              <span className="hint">
                Row {idx + 1} of {rows.length}
                {missing.length > 0 && <span className="danger-text"> · missing {missing.map(shortName).join(", ")}</span>}
              </span>
              <button className="ui-btn ui-btn-ghost ui-btn-icon" onClick={onRemove} aria-label={`Remove row ${idx + 1}`}>
                <IconTrash size={15} />
              </button>
            </div>

            <div className="field grow-field">
              <label htmlFor="pe-question">Question</label>
              <textarea
                id="pe-question"
                value={row.question}
                onChange={(e) => setCell(idx, "question", e.target.value)}
                placeholder="What the agent is asked."
                autoFocus
              />
            </div>
            <div className="field grow-field">
              <label htmlFor="pe-response">Ground truth response</label>
              <textarea
                id="pe-response"
                value={row.response}
                onChange={(e) => setCell(idx, "response", e.target.value)}
                placeholder="The answer a correct run should give."
              />
            </div>
            <div className="field grow-field">
              <label htmlFor="pe-reasoning">Ground truth reasoning</label>
              <textarea
                id="pe-reasoning"
                value={row.reasoning}
                onChange={(e) => setCell(idx, "reasoning", e.target.value)}
                placeholder="The steps a correct run should take to get there."
              />
            </div>
            <div className="preview-meta">
              <div className="field">
                <label htmlFor="pe-skill">Skill(s)</label>
                <input
                  id="pe-skill"
                  value={row.skill}
                  onChange={(e) => setCell(idx, "skill", e.target.value)}
                  placeholder="billing, reports"
                />
              </div>
              <div className="field">
                <label htmlFor="pe-qid">Question ID</label>
                <input
                  id="pe-qid"
                  className="mono"
                  value={row.question_id}
                  onChange={(e) => setCell(idx, "question_id", e.target.value)}
                  placeholder="generated on create"
                />
              </div>
            </div>
            <p className="hint preview-keys">⌘/Ctrl + ↑ ↓ to move between rows · Esc to collapse</p>
          </>
        )}
      </div>
    </div>
  );
}

// The wire names are long; in-place hints use the short form.
function shortName(field) {
  if (field === "ground_truth_response") return "response";
  if (field === "ground_truth_reasoning_process_description") return "reasoning";
  return field;
}
