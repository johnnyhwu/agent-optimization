import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import Modal from "./Modal.jsx";
import { useToast } from "./Toast.jsx";
import Button from "./ui/Button.jsx";

// Owner-only question editing (§6.11 locked set: edit only). Demonstrates the
// optimistic-lock 409 flow — version is held from load and sent on save.
export default function QuestionEditor({ evalSet, onClose }) {
  const toast = useToast();
  const [questions, setQuestions] = useState(null);
  const [active, setActive] = useState(null);
  const [draft, setDraft] = useState(null);
  const [error, setError] = useState(null);

  function load() {
    api.listQuestions(evalSet.id).then(setQuestions).catch((e) => setError(e.message));
  }
  useEffect(load, [evalSet.id]);

  function pick(q) {
    setActive(q);
    setDraft({ ...q });
    setError(null);
  }

  async function save() {
    setError(null);
    try {
      const updated = await api.updateQuestion(evalSet.id, active.id, {
        question: draft.question,
        ground_truth_response: draft.ground_truth_response,
        ground_truth_reasoning: draft.ground_truth_reasoning,
        version: active.version,
      });
      toast.success(`Saved · ${updated.question_id} kept, v→${updated.version}`);
      setActive(updated);
      setDraft({ ...updated });
      load();
    } catch (e) {
      if (e.status === 409) { setError("409 Conflict — " + e.message); toast.error("Conflict — reload"); }
      else setError(e.message);
    }
  }

  return (
    <Modal
      title={`Edit questions — ${evalSet.name}`}
      subtitle="Locked set: edit text only (no add/delete). question_id stays fixed; each save bumps version."
      onClose={onClose}
      width={760}
      footer={<Button onClick={onClose}>Close</Button>}
    >
      {error && <div className="error">{error}</div>}
      <div style={{ display: "flex", gap: 14 }}>
        <div style={{ width: 210, borderRight: "1px solid var(--border)", paddingRight: 8, maxHeight: 380, overflow: "auto" }}>
          {questions &&
            questions.map((q) => (
              <div key={q.id} className={`qitem ${active && active.id === q.id ? "active" : ""}`} onClick={() => pick(q)}>
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
              <div className="field">
                <label>question ({active.question_id}, v{active.version})</label>
                <textarea style={{ minHeight: 60 }} value={draft.question} onChange={(e) => setDraft({ ...draft, question: e.target.value })} />
              </div>
              <div className="field">
                <label>ground truth response</label>
                <textarea style={{ minHeight: 50 }} value={draft.ground_truth_response} onChange={(e) => setDraft({ ...draft, ground_truth_response: e.target.value })} />
              </div>
              <div className="field">
                <label>ground truth reasoning</label>
                <textarea style={{ minHeight: 60 }} value={draft.ground_truth_reasoning} onChange={(e) => setDraft({ ...draft, ground_truth_reasoning: e.target.value })} />
              </div>
              <Button variant="primary" onClick={save}>Save (v{active.version})</Button>
            </>
          )}
        </div>
      </div>
    </Modal>
  );
}
