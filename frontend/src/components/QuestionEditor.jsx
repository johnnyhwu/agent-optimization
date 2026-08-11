import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import Modal from "./Modal.jsx";
import { useToast } from "./Toast.jsx";
import Button from "./ui/Button.jsx";

// Owner-only question editing (§6.11 locked set: edit only). Demonstrates the
// optimistic-lock 409 flow — version is held from load and sent on save.
//
// The layout is `.pane-editor`, shared with the upload preview's expanded editor
// and the shortlist's review pane. All three are the same job — pick a question
// on the left, rewrite it on the right — and this one used to do it in a 760px
// box with three textareas hard-coded to 50-60px tall, which is smaller than the
// app's own default for a textarea. Rewriting the expected process for a
// question is not a task anyone should do through a letterbox, so the dialog
// takes the height it needs and the three fields split it.
export default function QuestionEditor({ evalSet, onClose }) {
  const toast = useToast();
  const [questions, setQuestions] = useState(null);
  const [active, setActive] = useState(null);
  const [draft, setDraft] = useState(null);
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);

  function load() {
    api
      .listQuestions(evalSet.id)
      .then((qs) => {
        setQuestions(qs);
        // Open on the first question rather than on an empty pane. Nobody comes
        // here to look at a list — they came to change some text, and a dialog
        // that fills the screen to show nothing until you click is asking for a
        // click it does not need.
        setActive((a) => a ?? qs[0] ?? null);
        setDraft((d) => d ?? (qs[0] ? { ...qs[0] } : null));
      })
      .catch((e) => setError(e.message));
  }
  useEffect(load, [evalSet.id]);

  function pick(q) {
    setActive(q);
    setDraft({ ...q });
    setError(null);
  }

  async function save() {
    setError(null);
    setSaving(true);
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
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      title={`Edit questions — ${evalSet.name}`}
      subtitle="Locked set: edit text only (no add/delete). question_id stays fixed; each save bumps version."
      onClose={onClose}
      width="min(1100px, 96vw)"
      height="92vh"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Close</Button>
          {/* The version is on the button because it is what the save asserts:
              "the copy I loaded is still the current one". A 409 here means
              someone else got there first, and the number is what says so. */}
          <Button variant="primary" onClick={save} disabled={!draft} loading={saving}>
            {active ? `Save (v${active.version})` : "Save"}
          </Button>
        </>
      }
    >
      {error && <div className="error">{error}</div>}

      <div className="field field-fill">
        <div className="pane-editor">
          <div className="pane-list">
            <div className="pane-list-rows">
              {questions &&
                questions.map((q, i) => (
                  <button
                    key={q.id}
                    className={`pane-item${active && active.id === q.id ? " active" : ""}`}
                    onClick={() => pick(q)}
                    aria-current={Boolean(active && active.id === q.id)}
                  >
                    <span className="n">{i + 1}</span>
                    <span className="grow">
                      {/* Whole question, elided by the column's width rather
                          than chopped at 30 characters — which was not enough
                          to tell two questions about the same table apart. */}
                      <span className="qtext" title={q.question}>{q.question}</span>
                      <span className="qid">{q.question_id} · v{q.version}</span>
                    </span>
                  </button>
                ))}
              {questions && questions.length === 0 && (
                <p className="hint">This set has no questions.</p>
              )}
            </div>
          </div>

          <div className="pane-fields">
            {!draft ? (
              <div className="upload-empty">Select a question on the left to edit it.</div>
            ) : (
              <>
                <div className="field grow-field grow-field-sm">
                  <label htmlFor="qe-question">
                    Question <span className="hint">· {active.question_id}, v{active.version}</span>
                  </label>
                  <textarea
                    id="qe-question"
                    value={draft.question}
                    onChange={(e) => setDraft({ ...draft, question: e.target.value })}
                  />
                </div>
                <div className="field grow-field">
                  <label htmlFor="qe-response">Ground truth response</label>
                  <textarea
                    id="qe-response"
                    value={draft.ground_truth_response}
                    onChange={(e) => setDraft({ ...draft, ground_truth_response: e.target.value })}
                  />
                </div>
                <div className="field grow-field">
                  <label htmlFor="qe-reasoning">Ground truth reasoning</label>
                  <textarea
                    id="qe-reasoning"
                    value={draft.ground_truth_reasoning}
                    onChange={(e) => setDraft({ ...draft, ground_truth_reasoning: e.target.value })}
                  />
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </Modal>
  );
}
