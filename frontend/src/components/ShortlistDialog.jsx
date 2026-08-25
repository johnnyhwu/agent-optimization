import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import Modal from "./Modal.jsx";
import ShareEditor from "./ShareEditor.jsx";
import { useToast } from "./Toast.jsx";
import { IconAlert, IconPlus, IconSparkles, IconX } from "./icons.jsx";
import { missingFields, toPayloadQuestion } from "../shortlist.js";
import Button from "./ui/Button.jsx";
import Badge from "./ui/Badge.jsx";

// Review shortlisted playground questions, then turn them into an eval set
// (§10.8).
//
// Two things here are not decoration:
//
// **The expected answer is prefilled with the agent's own, and says so.** A
// playground question usually has no ground truth — that is why it was asked
// here. Prefilling saves retyping; the chip stops it passing as a verified fact.
// An eval set built from unverified answers still has a use (it asks "does the
// agent still do this?"), but it cannot answer "is the agent right?", and those
// two look identical in a pass rate.
//
// **The expected process is drafted on a button, never automatically.** The
// draft describes what the agent did; whether that is what it *should* do is a
// judgement, and the diagnosis step later compares real traces against this
// text. Generating it for every item would also be a real LLM bill for drafts
// nobody asked for.
//
// **The dialog is two tabs, because it is two jobs.** Reviewing what each
// question asserts and deciding what the new set is called are done in sequence,
// not together — and sharing one scrollbar between them left the three fields
// that matter (question, expected answer, expected process) squeezed into ~90px
// each above a page of set-level form. Split, the review pane gets the whole
// dialog height. The footer's counts stay on both tabs so switching away never
// hides an unfinished question.
const TABS = [
  ["questions", "Questions"],
  ["details", "Eval set details"],
];

export default function ShortlistDialog({
  items, subject, onChange, onRemove, onClose, onCreated,
}) {
  const toast = useToast();
  const [tab, setTab] = useState("questions");
  const [activeId, setActiveId] = useState(items[0]?.id || null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [metaRows, setMetaRows] = useState([{ k: "", v: "" }]);
  const [shares, setShares] = useState([]);
  const [includeIds, setIncludeIds] = useState([]);
  const [sets, setSets] = useState([]);
  const [setsError, setSetsError] = useState(null);
  const [knownKeys, setKnownKeys] = useState([]);
  const [synthesizing, setSynthesizing] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const nameRef = useRef(null);

  useEffect(() => {
    api
      .listEvalSets({ limit: 100 })
      .then((page) => setSets(page.items || []))
      .catch((e) => setSetsError(e.message));
    api.metadataKeys().then(setKnownKeys).catch(() => {});
  }, []);

  const active = items.find((i) => i.id === activeId) || null;
  const incomplete = items.filter((i) => missingFields(i).length > 0);
  const includedCount = includeIds.length;

  function patch(id, fields) {
    onChange(id, fields);
  }

  async function synthesize(item) {
    setSynthesizing(item.id);
    setError(null);
    try {
      const out = await api.synthesizeReasoning(item.id);
      patch(item.id, {
        ground_truth_reasoning: out.reasoning_process,
        reasoning_from_synthesis: true,
      });
      toast.success(`Drafted with ${out.model_used}`);
    } catch (e) {
      // The model's own words, or "this attempt has no trace yet" — both are
      // things the developer can act on, unlike a generic failure.
      setError(e.message);
      toast.error(e.message);
    } finally {
      setSynthesizing(null);
    }
  }

  async function create() {
    setError(null);
    // Each check lands the developer on the thing it is about. A message about
    // an empty name is no use on the tab that does not contain the name field.
    if (!name.trim()) {
      setTab("details");
      // After the tab has rendered, or there is nothing to focus yet.
      setTimeout(() => nameRef.current?.focus(), 0);
      return setError("Give the new eval set a name.");
    }
    if (incomplete.length) {
      setTab("questions");
      setActiveId(incomplete[0].id);
      return setError(
        `Fill in every field first — ${incomplete.length} question(s) are incomplete.`
      );
    }
    if (!items.length && !includeIds.length) {
      return setError("A new eval set needs at least one question.");
    }

    const metadata = {};
    metaRows.forEach((r) => {
      if (r.k.trim()) metadata[r.k.trim()] = r.v;
    });

    setBusy(true);
    try {
      const out = await api.createEvalSetFromShortlist({
        name,
        description,
        metadata,
        shares,
        questions: items.map(toPayloadQuestion),
        include_eval_set_ids: includeIds,
      });
      toast.success(
        out.duplicates_skipped
          ? `Created with ${out.question_count} questions (${out.duplicates_skipped} duplicate(s) skipped)`
          : `Created with ${out.question_count} questions`
      );
      onCreated(out.id);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title="Shortlist"
      subtitle="Review what these questions will assert, then create an eval set from them."
      onClose={busy ? () => {} : onClose}
      width={1040}
      height="92vh"
      footer={
        <>
          <span className="muted shortlist-foot">
            {items.length} shortlisted
            {includedCount > 0 && ` + questions from ${includedCount} eval set(s)`}
            {incomplete.length > 0 && (
              <span className="error-text"> · {incomplete.length} incomplete</span>
            )}
          </span>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button
            variant="primary"
            icon={<IconPlus size={14} />}
            onClick={create}
            loading={busy}
            disabled={busy || (!items.length && !includeIds.length)}
          >
            {busy ? "Creating…" : "Create eval set"}
          </Button>
        </>
      }
    >
      {error && <div className="error" style={{ marginBottom: 12 }}>{error}</div>}

      <div className="ui-segmented shortlist-tabs" role="tablist">
        {TABS.map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={tab === id}
            className={tab === id ? "is-active" : ""}
            onClick={() => setTab(id)}
          >
            {label}
            {id === "questions" && items.length > 0 && (
              <span className="ui-segmented-count">{items.length}</span>
            )}
          </button>
        ))}
      </div>

      {tab === "questions" && (
        items.length === 0 ? (
          <div className="hint">
            Nothing shortlisted yet. Add an attempt from the list on the left of the
            playground — you can still create a set purely from existing ones under
            Eval set details.
          </div>
        ) : (
          <div className="field field-fill">
            <div className="pane-editor">
              <div className="pane-list">
                <div className="pane-list-rows">
                  {items.map((item) => {
                    const missing = missingFields(item);
                    return (
                      <button
                        key={item.id}
                        className={`shortlist-item${activeId === item.id ? " active" : ""}`}
                        onClick={() => setActiveId(item.id)}
                        aria-current={activeId === item.id}
                      >
                        {/* Clamped by CSS, not sliced at 70 characters — which
                            cut mid-word and got no better on a wider dialog. */}
                        <span className="q">{item.question || "(no question)"}</span>
                        <span className="ui-badge-row">
                          {missing.length > 0 && (
                            <Badge tone="warning">{missing.length} missing</Badge>
                          )}
                          {item.workspace_overridden && <Badge tone="warning">edited workspace</Badge>}
                          {item.answer_from_agent && <Badge tone="neutral">agent answer</Badge>}
                        </span>
                        <span
                          className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-destructive-hover"
                          role="button"
                          tabIndex={0}
                          title="Remove from the shortlist"
                          onClick={(e) => {
                            e.stopPropagation();
                            onRemove(item.id);
                            if (activeId === item.id) setActiveId(null);
                          }}
                        >
                          <IconX size={13} />
                        </span>
                      </button>
                    );
                  })}
                </div>
              </div>

              <div className="pane-fields">
                {active ? (
                  <>
                    {active.workspace_overridden && (
                      <div className="hint warn-text">
                        <IconAlert size={13} /> This attempt ran against edited
                        skill files ({(active.edited_skill_files || [])
                          .slice(0, 3)
                          .join(", ")}
                        {(active.edited_skill_files || []).length > 3 ? "…" : ""}
                        ). The deployed agent has none of those edits, so it may not be
                        able to produce this answer until you apply them on the agent
                        server yourself.
                      </div>
                    )}

                    {/* A question is a sentence; the two below it are paragraphs
                        someone is being asked to write. They split the height,
                        this one does not compete for it. */}
                    <div className="field grow-field grow-field-sm">
                      <label htmlFor="sl-question">Question</label>
                      <textarea
                        id="sl-question"
                        value={active.question}
                        onChange={(e) => patch(active.id, { question: e.target.value })}
                      />
                    </div>

                    <div className="field grow-field">
                      {/* The label labels the control; the badge and the action sit
                          beside it rather than inside it. A <button> nested in a
                          <label> is activated by clicks meant for the field. */}
                      <div className="field-head">
                        <label htmlFor="sl-answer">Expected answer</label>
                        {active.answer_from_agent && (
                          <Badge tone="warning">the agent's own — unverified</Badge>
                        )}
                      </div>
                      <textarea
                        id="sl-answer"
                        value={active.ground_truth_response}
                        placeholder="What a correct answer must say."
                        onChange={(e) =>
                          patch(active.id, {
                            ground_truth_response: e.target.value,
                            answer_from_agent: false,
                          })
                        }
                      />
                      {active.answer_from_agent && (
                        <div className="hint">
                          Check this before creating the set. Kept as-is, this question
                          asserts that the agent's current answer is the right one — it
                          will always pass, and it cannot catch the answer being wrong.
                        </div>
                      )}
                    </div>

                    <div className="field grow-field">
                      <div className="field-head">
                        <label htmlFor="sl-reasoning">Expected reasoning process</label>
                        {active.reasoning_from_synthesis && (
                          <Badge tone="warning">drafted from the trace</Badge>
                        )}
                        <div className="grow" />
                        <Button
                          variant="link"
                          icon={<IconSparkles size={12} />}
                          onClick={() => synthesize(active)}
                          disabled={synthesizing === active.id}
                        >
                          {synthesizing === active.id ? "Drafting…" : "Draft from trace"}
                        </Button>
                      </div>
                      <textarea
                        id="sl-reasoning"
                        value={active.ground_truth_reasoning}
                        placeholder="1. Read the billing skill. 2. Queried invoices for the period. 3. Presented the total."
                        onChange={(e) =>
                          patch(active.id, {
                            ground_truth_reasoning: e.target.value,
                            reasoning_from_synthesis: false,
                          })
                        }
                      />
                      <div className="hint">
                        The diagnosis compares future traces against this. A draft
                        describes what the agent did that once — edit it into what
                        should happen every time.
                      </div>
                    </div>

                    <div className="field">
                      <label htmlFor="sl-skills">Skill tags — optional, comma separated</label>
                      <input
                        id="sl-skills"
                        value={active.skills}
                        placeholder="billing, reporting"
                        onChange={(e) => patch(active.id, { skills: e.target.value })}
                      />
                    </div>
                  </>
                ) : (
                  <div className="upload-empty">Pick a question on the left to review it.</div>
                )}
              </div>
            </div>
          </div>
        )
      )}

      {tab === "details" && (
        <div className="shortlist-newset">
          <div className="field">
            <label htmlFor="sl-name">Name</label>
            <input
              id="sl-name"
              ref={nameRef}
              value={name}
              placeholder="e.g. billing regressions, August"
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="sl-desc">Description — optional</label>
            <input id="sl-desc" value={description} onChange={(e) => setDescription(e.target.value)} />
          </div>

          <div className="field">
            <label>
              Custom metadata — optional
              {knownKeys.length > 0 && <span className="hint"> · known: {knownKeys.join(", ")}</span>}
            </label>
            {metaRows.map((r, i) => (
              <div key={i} className="meta-row">
                <input
                  list="shortlist-known-keys"
                  placeholder="key"
                  aria-label={`Metadata key ${i + 1}`}
                  value={r.k}
                  onChange={(e) =>
                    setMetaRows((rs) => rs.map((x, j) => (j === i ? { ...x, k: e.target.value } : x)))
                  }
                />
                <input
                  placeholder="value"
                  aria-label={`Metadata value ${i + 1}`}
                  value={r.v}
                  onChange={(e) =>
                    setMetaRows((rs) => rs.map((x, j) => (j === i ? { ...x, v: e.target.value } : x)))
                  }
                />
              </div>
            ))}
            <datalist id="shortlist-known-keys">
              {knownKeys.map((k) => (
                <option key={k} value={k} />
              ))}
            </datalist>
            <Button size="sm" icon={<IconPlus size={14} />} onClick={() => setMetaRows((rs) => [...rs, { k: "", v: "" }])}>
              Add label
            </Button>
          </div>

          <div className="field">
            <label>Also include the questions of — optional</label>
            <div className="hint">
              An eval set is locked once created, so growing one means creating a new
              one. Questions are copied, and the sets you pick are left untouched.
            </div>
            {setsError ? (
              <div className="hint error-text">Could not list eval sets: {setsError}</div>
            ) : (
              <div className="include-sets">
                {sets.map((s) => (
                  <label key={s.id} className="include-row">
                    <input
                      type="checkbox"
                      checked={includeIds.includes(s.id)}
                      onChange={(e) =>
                        setIncludeIds((ids) =>
                          e.target.checked ? [...ids, s.id] : ids.filter((x) => x !== s.id)
                        )
                      }
                    />
                    <span className="nm">{s.name}</span>
                    <span className="muted">{s.my_role}</span>
                  </label>
                ))}
                {sets.length === 0 && <div className="hint">No other eval sets yet.</div>}
              </div>
            )}
          </div>

          <div className="field">
            <label>Share with — optional</label>
            <ShareEditor shares={shares} setShares={setShares} currentUser={subject} />
          </div>
        </div>
      )}
    </Modal>
  );
}
