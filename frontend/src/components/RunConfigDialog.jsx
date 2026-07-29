import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import Modal from "./Modal.jsx";
import RunPicker from "./RunPicker.jsx";
import { IconPlay } from "./icons.jsx";

// Config for one run (§9.2 seams), chosen at trigger time instead of baked into
// the deployment's environment. Prefilled from GET /run-config/defaults so the
// form and the server-side fallback always agree.
//
// Secrets are write-only: the backend never sends them back, so the two key
// fields start blank. "Use config from" fills the non-secret fields from an
// earlier run and asks the backend to carry that run's credentials over
// server-side — which it only does while the matching endpoint is unchanged, so
// the hint below tells the developer when a key still has to be retyped.
const SECRET_PAIRS = [
  ["llm_api_key", "llm_base_url"],
  ["langfuse_secret_key", "langfuse_host"],
];

export default function RunConfigDialog({ evalSetId, onClose, onRun }) {
  const [defaults, setDefaults] = useState(null);
  const [impls, setImpls] = useState({});
  const [form, setForm] = useState(null);
  const [secrets, setSecrets] = useState({ llm_api_key: "", langfuse_secret_key: "" });
  const [reuseFrom, setReuseFrom] = useState("");
  // The run behind `reuseFrom`, kept here because RunPicker only ever holds the
  // page it fetched and the endpoint-match rule below needs the run's config.
  const [source, setSource] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .runConfigDefaults()
      .then((r) => {
        setDefaults(r.defaults);
        setImpls(r.impls || {});
        setForm({ name: new Date().toLocaleString(), ...r.defaults });
      })
      .catch((e) => setError(e.message));
  }, []);

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  // A cleared number input parses to 0/NaN, which the backend would reject
  // (timeouts and concurrency are >= 1). Send null instead and let it fall back
  // to the env default, which is what an empty field means everywhere else here.
  const setNum = (k, raw) => {
    const n = Number(raw);
    set(k, raw === "" || !Number.isFinite(n) || n <= 0 ? null : n);
  };

  // The source run's credentials only carry over while their endpoint matches.
  const needsRetype = useMemo(() => {
    if (!source || !form) return [];
    return SECRET_PAIRS.filter(
      ([secret, endpoint]) =>
        !secrets[secret] && (form[endpoint] || "") !== (source.config?.[endpoint] || "")
    ).map(([secret]) => secret);
  }, [source, form, secrets]);

  function applyReuse(runId, run) {
    setReuseFrom(runId);
    setSource(run);
    if (!run) {
      // Back to the environment defaults, undoing whatever a previous pick
      // copied in — otherwise "start from the defaults" silently keeps them.
      setForm((f) => ({ ...(defaults || {}), name: f.name }));
      return;
    }
    // Keep the name (this is a new run) and only take the settings that run used;
    // anything it left blank falls back to the env default we started from.
    setForm((f) => {
      const next = { ...f };
      Object.keys(defaults || {}).forEach((k) => {
        const v = run.config?.[k];
        if (v !== undefined && v !== null && v !== "") next[k] = v;
      });
      return next;
    });
  }

  async function submit() {
    setError(null);
    setBusy(true);
    const { name, ...config } = form;
    try {
      await onRun({
        name,
        config,
        secrets,
        reuse_secrets_from_run_id: reuseFrom || null,
      });
    } catch (e) {
      setError(e.message);
      setBusy(false);
    }
  }

  const fake = (seam) => impls[seam] === "fake";
  // Only promise a borrowed key when it will actually be carried over.
  const kept = (secretKey) =>
    source && !needsRetype.includes(secretKey) ? "kept from the selected run" : "";

  return (
    <Modal
      title="Run eval"
      subtitle="These settings apply to this run only."
      onClose={onClose}
      width={620}
      footer={
        <>
          <button onClick={onClose}>Cancel</button>
          <button className="primary" disabled={busy || !form} onClick={submit}>
            <IconPlay size={14} /> {busy ? "Starting…" : "Run eval"}
          </button>
        </>
      }
    >
      {error && <div className="error">{error}</div>}
      {!form && <p className="muted">Loading defaults…</p>}

      {form && (
        <>
          <div className="field">
            <label>Use config from</label>
            <RunPicker evalSetId={evalSetId} value={reuseFrom} onChange={applyReuse} />
            {reuseFrom && (
              <div className="hint">
                {needsRetype.length === 0
                  ? "That run's keys carry over — no need to retype them."
                  : `Endpoint changed, so re-enter: ${needsRetype
                      .map((k) => (k === "llm_api_key" ? "LLM API Key" : "Langfuse Secret Key"))
                      .join(", ")}.`}
              </div>
            )}
          </div>


          <div className="field">
            <label>Run name</label>
            <input value={form.name} onChange={(e) => set("name", e.target.value)} autoFocus />
          </div>

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
          <div className="field">
            <label>Concurrency</label>
            <input
              type="number" min="1"
              value={form.concurrency ?? ""}
              onChange={(e) => setNum("concurrency", e.target.value)}
            />
            <div className="hint">How many questions are sent to the agent at once.</div>
          </div>

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
              onChange={(e) =>
                setSecrets((s) => ({ ...s, langfuse_secret_key: e.target.value }))
              }
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
      )}
    </Modal>
  );
}
