import React, { useEffect, useState } from "react";
import { api } from "../api.js";

// Owner-only question editing (§6.11 locked set: edit only). Demonstrates the
// optimistic-lock 409 flow — version is held from load and sent on save.
export default function QuestionEditor({ evalSet, onClose }) {
  const [questions, setQuestions] = useState(null);
  const [active, setActive] = useState(null);
  const [draft, setDraft] = useState(null);
  const [error, setError] = useState(null);
  const [status, setStatus] = useState(null);

  function load() {
    api.listQuestions(evalSet.id).then(setQuestions).catch((e) => setError(e.message));
  }
  useEffect(load, [evalSet.id]);

  function pick(q) {
    setActive(q);
    setDraft({ ...q });
    setError(null);
    setStatus(null);
  }

  async function save() {
    setError(null);
    setStatus(null);
    try {
      const updated = await api.updateQuestion(evalSet.id, active.id, {
        question: draft.question,
        ground_truth_response: draft.ground_truth_response,
        ground_truth_reasoning: draft.ground_truth_reasoning,
        version: active.version, // held-at-load version -> 409 if stale
      });
      setStatus(`Saved. question_id ${updated.question_id} unchanged, version → ${updated.version}.`);
      setActive(updated);
      setDraft({ ...updated });
      load();
    } catch (e) {
      if (e.status === 409) setError("409 Conflict — " + e.message);
      else setError(e.message);
    }
  }

  return (
    <div className="overlay" onClick={onClose}>
      <div className="dialog" style={{ width: 720 }} onClick={(e) => e.stopPropagation()}>
        <h3>Edit questions — {evalSet.name}</h3>
        <p className="muted">The question set is locked: you can edit text but not add/delete. question_id stays fixed; each save bumps version.</p>
        {error && <div className="error">{error}</div>}
        {status && <div className="improve">{status}</div>}
        <div style={{ display: "flex", gap: 12 }}>
          <div style={{ width: 200, borderRight: "1px solid var(--border)", paddingRight: 8, maxHeight: 360, overflow: "auto" }}>
            {questions &&
              questions.map((q) => (
                <div
                  key={q.id}
                  className={`qitem ${active && active.id === q.id ? "active" : ""}`}
                  onClick={() => pick(q)}
                >
                  <div>
                    <div style={{ fontSize: 12 }}>{q.question.slice(0, 30)}</div>
                    <div className="qid">{q.question_id} · v{q.version}</div>
                  </div>
                </div>
              ))}
          </div>
          <div style={{ flex: 1 }}>
            {!draft && <p className="muted">Select a question to edit.</p>}
            {draft && (
              <>
                <div className="label muted">question ({active.question_id}, v{active.version})</div>
                <textarea style={{ minHeight: 60 }} value={draft.question} onChange={(e) => setDraft({ ...draft, question: e.target.value })} />
                <div className="label muted">ground truth response</div>
                <textarea style={{ minHeight: 50 }} value={draft.ground_truth_response} onChange={(e) => setDraft({ ...draft, ground_truth_response: e.target.value })} />
                <div className="label muted">ground truth reasoning</div>
                <textarea style={{ minHeight: 60 }} value={draft.ground_truth_reasoning} onChange={(e) => setDraft({ ...draft, ground_truth_reasoning: e.target.value })} />
                <div className="actions">
                  <button className="primary" onClick={save}>Save (v{active.version})</button>
                </div>
              </>
            )}
          </div>
        </div>
        <div className="actions">
          <button onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}
