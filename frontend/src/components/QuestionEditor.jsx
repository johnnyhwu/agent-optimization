import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import Modal from "./Modal.jsx";
import { parseSkillCell, skillToText } from "../upload_parse.js";
import { skillNote } from "../skill_tags.js";
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

  // The tags live in the draft as the text the owner is typing, not as the
  // array they parse to — same shape, and the same `parseSkillCell`, as the
  // upload table's skill cell, so "billing, reports" means here what it meant in
  // the file this set came from.
  const asDraft = (q) => ({ ...q, skillText: skillToText(q.skills) });

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
        setDraft((d) => d ?? (qs[0] ? asDraft(qs[0]) : null));
      })
      .catch((e) => setError(e.message));
  }
  useEffect(load, [evalSet.id]);

  function pick(q) {
    setActive(q);
    setDraft(asDraft(q));
    setError(null);
  }

  const skills = draft ? parseSkillCell(draft.skillText) : [];
  // Only for the two shapes that leave a question out of every skill group —
  // see skill_tags.js.
  const note = skillNote(skills);

  async function save() {
    setError(null);
    setSaving(true);
    try {
      const updated = await api.updateQuestion(evalSet.id, active.id, {
        question: draft.question,
        ground_truth_response: draft.ground_truth_response,
        ground_truth_reasoning: draft.ground_truth_reasoning,
        // Sent every save, so a tag deleted in the box is a tag deleted on the
        // question. Omitting the field is how the API says "leave them alone",
        // which is not what an empty box means here.
        skills,
        version: active.version,
      });
      toast.success(`Saved · ${updated.question_id} kept, v→${updated.version}`);
      setActive(updated);
      // From the response rather than from the draft: the server strips, drops
      // blanks and de-duplicates, so this is what the box should now read.
      setDraft(asDraft(updated));
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
      subtitle="Locked set: edit only (no add/delete). question_id stays fixed; each save bumps version."
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
                {/* The upload's fourth column, and the field that decides
                    which skill group an optimization run files this question
                    under. A comma-separated box rather than chips, because that
                    is what the upload table used and what an owner re-reading
                    their own file expects to type. */}
                <div className="field">
                  <label htmlFor="qe-skills">Skills</label>
                  <input
                    id="qe-skills"
                    value={draft.skillText}
                    placeholder="billing, reports"
                    onChange={(e) => setDraft({ ...draft, skillText: e.target.value })}
                  />
                  <p className="hint">
                    {note || "Comma separated. Names must match the agent's skill directories."}
                  </p>
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </Modal>
  );
}
