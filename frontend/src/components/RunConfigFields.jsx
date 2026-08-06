import React from "react";

// The three connection sections shared by "Run eval" (RunConfigDialog) and the
// playground's config panel. Extracted rather than copied: the parts worth
// getting right — greying out a seam that is still `fake`, and keeping the two
// key fields write-only — are exactly the parts that rot when duplicated.
//
// Props are the caller's form state, so each host keeps its own submit shape:
//   form/set/setNum        the nine non-secret settings
//   secrets/setSecrets     write-only; the backend never sends these back
//   impls                  {agent,judge,trace,diagnosis,workspace} -> 'fake' | 'real'
//   kept(secretKey)        placeholder text when a key is being carried over
//   showAgent              false where the agent is chosen elsewhere
//   showConcurrency        false for a single question, where it means nothing
//
// `showAgent` is off in the playground, where picking an agent is a connection
// step with its own bar rather than a field: the workspace it edits is read from
// that server, so choosing it has to happen before anything else on the screen
// means much. A run has no such step — it is triggered and gone — so the dialog
// keeps the fields. The fields themselves are not duplicated for that: this
// component still owns them, and the playground simply asks for the rest.
export default function RunConfigFields({
  form,
  set,
  setNum,
  secrets,
  setSecrets,
  impls = {},
  kept = () => "",
  showAgent = true,
  showConcurrency = true,
}) {
  const fake = (seam) => impls[seam] === "fake";

  return (
    <>
      {showAgent && (
        <>
          <h4 className="cfg-section">
            Agent {fake("agent") && <span className="hint">— AGENT_IMPL=fake, not used</span>}
          </h4>
          <div className="field">
            <label>Agent Base URL</label>
            <input
              value={form.agent_base_url}
              placeholder="http://agent-host:8080"
              disabled={fake("agent")}
              onChange={(e) => set("agent_base_url", e.target.value)}
            />
          </div>
          <div className="field">
            <label>Agent Timeout (sec)</label>
            <input
              type="number" min="1"
              value={form.agent_timeout_s ?? ""}
              disabled={fake("agent")}
              onChange={(e) => setNum("agent_timeout_s", e.target.value)}
            />
          </div>
        </>
      )}
      {showConcurrency && (
        <div className="field">
          <label>Concurrency</label>
          <input
            type="number" min="1"
            value={form.concurrency ?? ""}
            onChange={(e) => setNum("concurrency", e.target.value)}
          />
          {/* Never disabled: this is orchestration, not a seam. */}
          <div className="hint">How many questions are sent to the agent at once.</div>
        </div>
      )}

      <h4 className="cfg-section">
        Langfuse {fake("trace") && <span className="hint">— TRACE_IMPL=fake, not used</span>}
      </h4>
      <div className="field">
        <label>Langfuse Host</label>
        <input
          value={form.langfuse_host}
          disabled={fake("trace")}
          onChange={(e) => set("langfuse_host", e.target.value)}
        />
      </div>
      <div className="field">
        <label>Langfuse Public Key</label>
        <input
          value={form.langfuse_public_key}
          disabled={fake("trace")}
          onChange={(e) => set("langfuse_public_key", e.target.value)}
        />
      </div>
      <div className="field">
        <label>Langfuse Secret Key</label>
        <input
          type="password" autoComplete="new-password"
          value={secrets.langfuse_secret_key}
          placeholder={kept("langfuse_secret_key")}
          disabled={fake("trace")}
          onChange={(e) => setSecrets((s) => ({ ...s, langfuse_secret_key: e.target.value }))}
        />
      </div>
      <div className="field">
        <label>Langfuse Timeout (sec)</label>
        <input
          type="number" min="1"
          value={form.langfuse_timeout_s ?? ""}
          disabled={fake("trace")}
          onChange={(e) => setNum("langfuse_timeout_s", e.target.value)}
        />
      </div>

      <h4 className="cfg-section">
        LLM — judge &amp; diagnosis
        {fake("judge") && fake("diagnosis") && (
          <span className="hint">— both seams fake, not used</span>
        )}
      </h4>
      <div className="field">
        <label>LLM Base URL</label>
        <input
          value={form.llm_base_url}
          onChange={(e) => set("llm_base_url", e.target.value)}
        />
      </div>
      <div className="field">
        <label>LLM API Key</label>
        <input
          type="password" autoComplete="new-password"
          value={secrets.llm_api_key}
          placeholder={kept("llm_api_key")}
          onChange={(e) => setSecrets((s) => ({ ...s, llm_api_key: e.target.value }))}
        />
      </div>
      <div className="field">
        <label>Judge Model Name</label>
        <input
          value={form.judge_model}
          disabled={fake("judge")}
          onChange={(e) => set("judge_model", e.target.value)}
        />
      </div>
      <div className="field">
        <label>Diagnosis Model Name</label>
        <input
          value={form.diagnosis_model}
          disabled={fake("diagnosis")}
          onChange={(e) => set("diagnosis_model", e.target.value)}
        />
      </div>
    </>
  );
}
