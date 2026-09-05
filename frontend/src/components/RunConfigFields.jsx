import React from "react";
import Field, { FormSection } from "./ui/Field.jsx";
import Badge from "./ui/Badge.jsx";
import Banner from "./ui/Banner.jsx";
import AgentEndpointsFields from "./AgentEndpointsFields.jsx";
import Button from "./ui/Button.jsx";
import { IconCheck, IconRefresh } from "./icons.jsx";
import { plural } from "../plural.js";
import NumberInput from "./ui/NumberInput.jsx";

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

// What the skills read means for *this eval set* — and only that.
//
// The read itself now reports itself, beside the field that caused it
// (`AgentEndpointsFields`), which is where a connection error belongs. What is
// left here is the part that has nothing to do with the connection: how many
// skills came back, and whether this set's questions need ones the agent does
// not have. Those are claims about the run being started, and they read as a
// consequence of a successful read rather than as its status line.
//
// Keeping both in one block was what made a failed read say two different
// things in two places, one of them stale.
export function AgentProbe({ probe, coverage, onRetry }) {
  // Nothing to report about a seam that is not being asked: the section's
  // `simulated` badge has already said so, and a count here would be describing
  // a connection that was never made.
  if (probe.state === "simulated") return null;

  if (probe.state === "failed") {
    // The reason is already on screen under the field. What is not is a way
    // back from it: a read can fail because a server was restarting, and
    // retyping the URL to re-trigger the check is not a fix anyone should have
    // to discover.
    return onRetry ? (
      <div className="cfg-probe">
        <Button size="sm" icon={<IconRefresh size={13} />} onClick={onRetry}>
          Try again
        </Button>
      </div>
    ) : null;
  }

  // `none` — no skills endpoint — says its piece on the field's status line.
  // A count here would be describing a listing nobody asked for.
  if (probe.state !== "connected") return null;

  return (
    <div className="cfg-probe">
      <div className="ok-text">
        <IconCheck size={13} /> {plural(probe.skills.length, "skill")} on this agent
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
  // The expensive half, owned by the host too — it spends a model call, so only
  // a screen that knows when that is worth it may trigger one.
  chatProbe = null,
  chatBusy = false,
  onTestChat = null,
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
          <AgentEndpointsFields
            chatUrl={form.agent_chat_url || ""}
            skillsUrl={form.agent_skills_url || ""}
            onChangeChat={(v) => set("agent_chat_url", v)}
            onChangeSkills={(v) => set("agent_skills_url", v)}
            apiKey={secrets.agent_api_key}
            authHeader={form.agent_auth_header || ""}
            onChangeApiKey={(v) => setSecrets((s) => ({ ...s, agent_api_key: v }))}
            onChangeAuthHeader={(v) => set("agent_auth_header", v)}
            keptApiKey={kept("agent_api_key")}
            disabled={fake("agent")}
            chatProbe={chatProbe}
            chatBusy={chatBusy}
            onTestChat={onTestChat}
            skillsProbe={probe?.check
              ? { check: probe.check, request_preview: probe.request_preview,
                  response_preview: probe.response_preview }
              : null}
            skillsBusy={probe?.state === "checking"}
            idPrefix="run"
          />
          {probe && (
            <AgentProbe probe={probe} coverage={coverage} onRetry={onRetryProbe} />
          )}
          <Field label="Timeout" hint="seconds">
            <NumberInput
              min="1"
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
            <NumberInput
              min="1"
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
          <NumberInput
            min="1"
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
