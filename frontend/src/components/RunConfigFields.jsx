import React from "react";
import Field, { FormSection } from "./ui/Field.jsx";
import Badge from "./ui/Badge.jsx";

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

      <FormSection
        title="Grading & diagnosis models"
        description={
          fake("judge") && fake("diagnosis")
            ? simulatedNote
            : "The models that grade each answer and explain the wrong ones."
        }
        aside={fake("judge") && fake("diagnosis") && <SimulatedBadge />}
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
        <Field label="Diagnosis model" hint={fake("diagnosis") ? "simulated" : undefined}>
          <input
            value={form.diagnosis_model}
            disabled={fake("diagnosis")}
            onChange={(e) => set("diagnosis_model", e.target.value)}
          />
        </Field>
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
