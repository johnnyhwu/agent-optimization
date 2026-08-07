import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import Modal from "./Modal.jsx";
import RunConfigFields, { servicesSummary } from "./RunConfigFields.jsx";
import RunPicker from "./RunPicker.jsx";
import Button from "./ui/Button.jsx";
import Field, { Disclosure, FormSection } from "./ui/Field.jsx";
import Skeleton from "./ui/Skeleton.jsx";
import { IconGear, IconPlay } from "./icons.jsx";

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

export default function RunConfigDialog({ evalSetId, evalSet, onClose, onRun }) {
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
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" icon={<IconPlay size={14} />} disabled={!form} loading={busy} onClick={submit}>
            {busy ? "Starting…" : "Run eval"}
          </Button>
        </>
      }
    >
      {error && <div className="error">{error}</div>}
      {!form && <Skeleton variant="text" count={4} />}

      {form && (
        <>
          <Field label="Run name" help="Shown in the run history. Leave the timestamp if you have nothing better.">
            <input value={form.name} onChange={(e) => set("name", e.target.value)} autoFocus />
          </Field>

          <Field
            label="Start from an earlier run's settings"
            help={
              reuseFrom
                ? needsRetype.length === 0
                  ? "That run's keys carry over — no need to retype them."
                  : `Its endpoint changed, so re-enter: ${needsRetype
                      .map((k) => (k === "llm_api_key" ? "the LLM API key" : "the trace store secret key"))
                      .join(", ")}.`
                : undefined
            }
          >
            <RunPicker evalSetId={evalSetId} value={reuseFrom} onChange={applyReuse} />
          </Field>

          {/* Eleven connection fields used to sit open in front of anyone who
              only wanted to press the button, most of them greyed out and
              captioned with an environment-variable name. They are still all
              here — a run records the exact settings it was triggered with, and
              overriding one is a real need — but behind a summary that answers
              "do I need to look at this?" without being opened. */}
          <Disclosure
            summary="Connection settings"
            detail={servicesSummary(impls)}
            icon={<IconGear size={14} />}
          >
            <RunConfigFields
              form={form}
              set={set}
              setNum={setNum}
              secrets={secrets}
              setSecrets={setSecrets}
              impls={impls}
              kept={kept}
            />
          </Disclosure>

          {/* One line, not two textareas. The grading criteria belong to the
              eval set (only its owner may change them), so this dialog states
              which prompt the run will use and where to go to change it —
              putting the full text here would double the dialog's height for
              something nobody edits from this screen. */}
          {evalSet?.judge_prompt && (
            <FormSection title="Grading criteria">
              <div className="cfg-view">
                <div className="cfg-row">
                  <span className="cfg-label">Prompt</span>
                  <span className="cfg-value">
                    {evalSet.judge_prompt.is_default ? "built-in default" : "custom"} ·{" "}
                    {evalSet.judge_prompt.fingerprint}
                    {evalSet.judge_prompt.verified_at ? " · verified" : ""}
                  </span>
                </div>
              </div>
              <div className="hint" style={{ marginTop: 6 }}>
                {evalSet.judge_prompt.missing_placeholders?.length > 0 ? (
                  <span className="danger-text">
                    This set’s grading prompt is missing{" "}
                    {evalSet.judge_prompt.missing_placeholders
                      .map((p) => `{${p}}`)
                      .join(", ")}
                    . Results from this run will not mean what they appear to.
                  </span>
                ) : (
                  <>
                    Set by the eval set’s owner, so every run of this set is
                    graded the same way and their pass rates can be compared.
                    {!evalSet.judge_prompt.verified_at &&
                      " It has not been verified against a real judge model."}
                  </>
                )}
              </div>
            </FormSection>
          )}
        </>
      )}
    </Modal>
  );
}
