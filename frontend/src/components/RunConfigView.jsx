import React from "react";
import Modal from "./Modal.jsx";

// What one run was triggered with — read-only, because a finished run's settings
// are history. There is deliberately no input anywhere in here: the way to reuse
// these values is the "Run eval" dialog's "Use config from…", which starts a new
// run rather than rewriting this one.
//
// Since the previous change, trigger_run materializes the environment defaults
// into runs.config, so every field of a recent run shows a concrete value. Runs
// created before that predate the whole feature and carry an empty config; the
// note below says so rather than showing nine blanks.
const SECTIONS = [
  ["Agent", [
    ["agent_base_url", "Base URL"],
    ["agent_timeout_s", "Timeout (sec)"],
    ["concurrency", "Concurrency"],
  ]],
  ["Langfuse", [
    ["langfuse_host", "Host"],
    ["langfuse_public_key", "Public Key"],
    ["langfuse_timeout_s", "Timeout (sec)"],
  ]],
  ["LLM — judge & diagnosis", [
    ["llm_base_url", "Base URL"],
    ["judge_model", "Judge Model"],
    ["diagnosis_model", "Diagnosis Model"],
  ]],
];

// Credentials are write-only, so the value is never available here — only
// whether the run recorded one.
const CREDENTIALS = [
  ["llm", "LLM API Key"],
  ["langfuse", "Langfuse Secret Key"],
];

function Row({ label, value }) {
  const empty = value === undefined || value === null || value === "";
  return (
    <div className="cfg-row">
      <span className="cfg-label">{label}</span>
      <span className={`cfg-value ${empty ? "empty" : ""}`}>
        {empty ? "not set" : String(value)}
      </span>
    </div>
  );
}

export default function RunConfigView({ run, onClose }) {
  const config = run.config || {};
  const recorded = Object.values(config).some(
    (v) => v !== undefined && v !== null && v !== ""
  );
  const credentials = run.credentials_set || [];

  return (
    <Modal
      title="Run config"
      subtitle={`${run.name || new Date(run.started_at).toLocaleString()} · by ${run.triggered_by}`}
      onClose={onClose}
      width={560}
      footer={<button onClick={onClose}>Close</button>}
    >
      <div className="hint" style={{ marginBottom: 14 }}>
        The settings this run was started with. Read-only — a finished run's
        config is history. To run again with these values, use “Run eval” and
        pick this run under “Use config from”.
      </div>

      {!recorded && (
        <div className="empty">
          This run predates per-run config. It used whatever the server
          environment was set to at the time, which wasn’t recorded.
        </div>
      )}

      {recorded && (
        <>
          {SECTIONS.map(([title, fields]) => (
            <React.Fragment key={title}>
              <h4 className="cfg-section">{title}</h4>
              <div className="cfg-view">
                {fields.map(([key, label]) => (
                  <Row key={key} label={label} value={config[key]} />
                ))}
              </div>
            </React.Fragment>
          ))}

          {/* The words this run was graded by, frozen at trigger time. The eval
              set may have moved on since; a finished run's verdicts only make
              sense against the criteria that produced them, so this is the copy
              that matters and not the set's current one. */}
          <h4 className="cfg-section">Judging</h4>
          <div className="cfg-view">
            <Row label="Judge prompt" value={config.judge_prompt_fingerprint} />
          </div>
          {config.judge_system_prompt ? (
            <details style={{ marginTop: 6 }}>
              <summary className="linkish">View the judge prompt this run used</summary>
              <div className="hint" style={{ marginTop: 8 }}>System</div>
              <pre className="prompt-text">{config.judge_system_prompt}</pre>
              <div className="hint" style={{ marginTop: 8 }}>User</div>
              <pre className="prompt-text">{config.judge_user_prompt}</pre>
            </details>
          ) : (
            <div className="hint" style={{ marginTop: 6 }}>
              This run predates per-set judge prompts. It graded with whatever
              the built-in prompt was at the time.
            </div>
          )}

          <h4 className="cfg-section">Credentials</h4>
          <div className="cfg-view">
            {CREDENTIALS.map(([slot, label]) => (
              <div className="cfg-row" key={slot}>
                <span className="cfg-label">{label}</span>
                <span className={`cfg-value ${credentials.includes(slot) ? "" : "empty"}`}>
                  {credentials.includes(slot) ? "set" : "not set"}
                </span>
              </div>
            ))}
          </div>
          <div className="hint" style={{ marginTop: 8 }}>
            Keys are never sent back to the browser — only whether one was stored.
          </div>
        </>
      )}
    </Modal>
  );
}
