import React, { useState } from "react";
import RunConfigFields from "./RunConfigFields.jsx";
import SkillEditor from "./SkillEditor.jsx";
import { IconGear, IconSend } from "./icons.jsx";

// What gets sent: a question, optionally a skill to substitute, optionally the two
// ground-truth fields, and the connection settings.
//
// Only the question is required, and that is the point of the whole tab (§10.4).
// The two ground-truth boxes are switches — an expected answer turns judging on,
// an expected reasoning process turns diagnosis on — so they are collapsed by
// default with the consequence stated, rather than presented as a form to fill in
// before anything happens.
export default function PlaygroundComposer({
  draft, setDraft, form, set, setNum, secrets, setSecrets, impls, onSend, busy,
}) {
  const [showTruth, setShowTruth] = useState(
    Boolean(draft.ground_truth_response || draft.ground_truth_reasoning)
  );
  const [showConfig, setShowConfig] = useState(false);

  const field = (key) => (e) => setDraft({ ...draft, [key]: e.target.value });
  const canSend = draft.question.trim().length > 0 && !busy;

  return (
    <div className="composer">
      <div className="field">
        <label>Question</label>
        <textarea
          className="composer-question"
          value={draft.question}
          placeholder="Ask the agent one question…"
          onChange={field("question")}
          onKeyDown={(e) => {
            // Enter alone inserts a newline: questions are often multi-line, and
            // losing a half-typed one to a stray Enter would be worse than
            // needing a modifier.
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && canSend) onSend();
          }}
        />
      </div>

      <SkillEditor
        value={draft.skill_override}
        onChange={(skill_override) => setDraft({ ...draft, skill_override })}
        fakeSeam={impls.skill === "fake"}
      />

      <div className="composer-toggles">
        <button
          className={showTruth ? "active" : ""}
          onClick={() => setShowTruth((v) => !v)}
        >
          Expected answer &amp; process
          {(draft.ground_truth_response || draft.ground_truth_reasoning) && (
            <span className="count">set</span>
          )}
        </button>
        <button className={showConfig ? "active" : ""} onClick={() => setShowConfig((v) => !v)}>
          <IconGear size={13} /> Settings
        </button>
        <div className="grow" />
        <button className="primary" disabled={!canSend} onClick={onSend}>
          <IconSend size={14} /> {busy ? "Sending…" : "Ask the agent"}
        </button>
      </div>

      {showTruth && (
        <div className="composer-truth">
          <div className="field">
            <label>Expected answer — optional</label>
            <textarea
              value={draft.ground_truth_response || ""}
              placeholder="Leave blank to skip judging."
              onChange={field("ground_truth_response")}
            />
            <div className="hint">Given one, the judge grades the answer against it.</div>
          </div>
          <div className="field">
            <label>Expected reasoning process — optional</label>
            <textarea
              value={draft.ground_truth_reasoning || ""}
              placeholder="e.g. Read the billing skill, query invoices for the period, then sum the balances."
              onChange={field("ground_truth_reasoning")}
            />
            <div className="hint">
              Given one, the trace is diagnosed against it — coarse-grained clues,
              not a verdict (§6.9).
            </div>
          </div>
        </div>
      )}

      {showConfig && (
        <div className="composer-config">
          <RunConfigFields
            form={form}
            set={set}
            setNum={setNum}
            secrets={secrets}
            setSecrets={setSecrets}
            impls={impls}
            showConcurrency={false}
          />
          <div className="hint">
            These settings stay for the rest of this browser session, so keys are
            typed once. They are never sent back to the browser.
          </div>
        </div>
      )}
    </div>
  );
}
