import React from "react";
import Field, { FormSection } from "./ui/Field.jsx";
import Badge from "./ui/Badge.jsx";
import Banner from "./ui/Banner.jsx";
import Button from "./ui/Button.jsx";
import { IconAlert, IconCheck, IconRefresh } from "./icons.jsx";
import { plural } from "../plural.js";

// The three connection sections shared by "Run eval" (RunConfigDialog) and the
// playground's config panel. Extracted rather than copied: the parts worth
// getting right — marking a service that is still simulated, and keeping the two
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
// keeps the fields.
//
// A service that is still simulated used to be labelled with the environment
// variable that made it so: "AGENT_IMPL=fake, not used", printed on the section
// heading. That is a deployment detail leaking through the glass — it names a
// variable the reader cannot see, cannot set from here, and did not ask about.
// The state is real and worth showing; the variable name is not.

// What the pre-flight found, under the URL that caused it.
//
// Three states and they are not interchangeable:
//
//   checking    the Start button is disabled and this says why. Without a line
//               here, a button that will not depress reads as a broken dialog.
//   connected   the agent answered. The skill count is the evidence — a tick on
//               its own is a claim; "6 skills" is the thing that was read.
//   failed      the agent server's own words, verbatim. "This agent has no
//               skills" and "your URL is wrong" have to stay distinguishable,
//               and only the reason it gave can tell them apart. It stays on
//               screen rather than passing as a toast, because it is a state to
//               fix, not news — the same rule the playground's connection bar
//               follows.
//
// The coverage warning is a separate claim and reads as one: the connection
// succeeded, and *then* there is something about this eval set worth knowing.
export function AgentProbe({ probe, coverage, onRetry }) {
  // Nothing to report about a seam that is not being asked: the section's
  // `simulated` badge has already said so, and a tick here would be claiming a
  // connection that was never made.
  if (probe.state === "simulated") return null;

  if (probe.state === "checking") {
    return (
      <div className="cfg-probe hint">Checking the agent…</div>
    );
  }

  if (probe.state === "failed") {
    return (
      <div className="cfg-probe">
        <div className="error-text">
          <IconAlert size={13} /> Could not reach this agent.
        </div>
        <div className="hint cfg-probe-detail">{probe.error}</div>
        {onRetry && (
          <Button size="sm" icon={<IconRefresh size={13} />} onClick={onRetry}>
            Try again
          </Button>
        )}
      </div>
    );
  }

  return (
    <div className="cfg-probe">
      <div className="ok-text">
        <IconCheck size={13} /> Connected · {plural(probe.skills.length, "skill")}
        {probe.version ? <code className="agent-version">{probe.version}</code> : null}
      </div>
      {/* Heading and body both come from `coverageWarning`, so the heading
          cannot make a claim the sentence under it does not support. */}
      {coverage && (
        <Banner tone="warning" title={coverage.title}>
          {coverage.text}
        </Banner>
      )}
    </div>
  );
}

// The diagnosis model, as one field rather than as two copies of one.
//
// It has to appear in two places that group it differently: the run dialog puts
// it under its Trace diagnosis section, beside the checkbox that decides whether
// the model is called at all, while the playground has no such choice and keeps
// it with the other model settings. Sharing the field is what stops "simulated"
// being marked in one place and not the other the next time this is touched.
//
// `disabled` is the caller's to decide, because the reasons differ: the seam
// being fake here, the checkbox being unticked there.
export function DiagnosisModelField({ value, onChange, simulated, disabled }) {
  return (
    <Field label="Diagnosis model" hint={simulated ? "simulated" : undefined}>
      <input
        value={value}
        disabled={disabled ?? simulated}
        onChange={(e) => onChange(e.target.value)}
      />
    </Field>
  );
}

export function SimulatedBadge() {
  return (
    <Badge tone="neutral" title="Simulated for this environment — these settings are not used">
      simulated
    </Badge>
  );
}

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
  showDiagnosisModel = true,
  // The pre-flight's result, owned by the host (only the run dialog runs one).
  // Absent in the playground, which has a connection bar of its own.
  probe = null,
  coverage = null,
  onRetryProbe = null,
}) {
  const fake = (seam) => impls[seam] === "fake";
  const simulatedNote = "Simulated in this environment, so what you enter here has no effect.";

  return (
    <>
      {showAgent && (
        <FormSection
          title="Agent"
          description={fake("agent") ? simulatedNote : "The service that answers each question."}
          aside={fake("agent") && <SimulatedBadge />}
        >
          <Field label="Base URL">
            <input
              value={form.agent_base_url}
              placeholder="http://agent-host:8080"
              disabled={fake("agent")}
              onChange={(e) => set("agent_base_url", e.target.value)}
            />
          </Field>
          {probe && (
            <AgentProbe probe={probe} coverage={coverage} onRetry={onRetryProbe} />
          )}
          <Field label="Timeout" hint="seconds">
            <input
              type="number" min="1"
              value={form.agent_timeout_s ?? ""}
              disabled={fake("agent")}
              onChange={(e) => setNum("agent_timeout_s", e.target.value)}
            />
          </Field>
        </FormSection>
      )}
      {showConcurrency && (
        <FormSection title="Speed">
          {/* Never disabled: this is how the run is orchestrated, not one of the
              services it talks to. */}
          <Field label="Concurrency" help="How many questions are sent to the agent at once.">
            <input
              type="number" min="1"
              value={form.concurrency ?? ""}
              onChange={(e) => setNum("concurrency", e.target.value)}
            />
          </Field>
        </FormSection>
      )}

      <FormSection
        title="Trace store"
        description={
          fake("trace") ? simulatedNote : "Where the agent records what it did, step by step."
        }
        aside={fake("trace") && <SimulatedBadge />}
      >
        <Field label="Langfuse host">
          <input
            value={form.langfuse_host}
            disabled={fake("trace")}
            onChange={(e) => set("langfuse_host", e.target.value)}
          />
        </Field>
        <Field label="Public key">
          <input
            value={form.langfuse_public_key}
            disabled={fake("trace")}
            onChange={(e) => set("langfuse_public_key", e.target.value)}
          />
        </Field>
        <Field label="Secret key">
          <input
            type="password" autoComplete="new-password"
            value={secrets.langfuse_secret_key}
            placeholder={kept("langfuse_secret_key")}
            disabled={fake("trace")}
            onChange={(e) => setSecrets((s) => ({ ...s, langfuse_secret_key: e.target.value }))}
          />
        </Field>
        <Field label="Timeout" hint="seconds">
          <input
            type="number" min="1"
            value={form.langfuse_timeout_s ?? ""}
            disabled={fake("trace")}
            onChange={(e) => setNum("langfuse_timeout_s", e.target.value)}
          />
        </Field>
      </FormSection>

      {/* Titled by what it actually holds. The run dialog moves the diagnosis
          model out to sit beside the switch that decides whether it is used at
          all, and a section still called "Grading & diagnosis models" would then
          be naming a field one screen away. */}
      <FormSection
        title={showDiagnosisModel ? "Grading & diagnosis models" : "Grading model"}
        description={
          fake("judge") && (fake("diagnosis") || !showDiagnosisModel)
            ? simulatedNote
            : showDiagnosisModel
              ? "The models that grade each answer and explain the wrong ones."
              : "The model that grades each answer."
        }
        aside={
          fake("judge") && (fake("diagnosis") || !showDiagnosisModel) && <SimulatedBadge />
        }
      >
        <Field label="LLM base URL">
          <input value={form.llm_base_url} onChange={(e) => set("llm_base_url", e.target.value)} />
        </Field>
        <Field label="API key">
          <input
            type="password" autoComplete="new-password"
            value={secrets.llm_api_key}
            placeholder={kept("llm_api_key")}
            onChange={(e) => setSecrets((s) => ({ ...s, llm_api_key: e.target.value }))}
          />
        </Field>
        <Field label="Grading model" hint={fake("judge") ? "simulated" : undefined}>
          <input
            value={form.judge_model}
            disabled={fake("judge")}
            onChange={(e) => set("judge_model", e.target.value)}
          />
        </Field>
        {showDiagnosisModel && (
          <DiagnosisModelField
            value={form.diagnosis_model}
            onChange={(v) => set("diagnosis_model", v)}
            simulated={fake("diagnosis")}
          />
        )}
      </FormSection>
    </>
  );
}

// One line describing what the run will actually talk to, for the closed state of
// the "Advanced" disclosure. Someone who only wants to press the button should be
// able to satisfy themselves without opening anything.
export function servicesSummary(impls = {}) {
  const simulated = ["agent", "judge", "trace", "diagnosis"].filter((s) => impls[s] === "fake");
  if (simulated.length === 0) return "Using this environment's configured services";
  if (simulated.length === 4) return "Demo mode — every service is simulated";
  return `Using this environment's services · ${simulated.length} simulated`;
}
