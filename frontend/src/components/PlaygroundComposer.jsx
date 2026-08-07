import React, { useState } from "react";
import RunConfigFields from "./RunConfigFields.jsx";
import WorkspaceEditor from "./WorkspaceEditor.jsx";
import Badge from "./ui/Badge.jsx";
import Button from "./ui/Button.jsx";
import Drawer from "./ui/Drawer.jsx";
import { diffConfig, editedFiles, flattenLeaves } from "../workspace_util.js";
import {
  IconAlert, IconBeaker, IconFileText, IconGear, IconSend, IconTarget,
} from "./icons.jsx";

// What gets sent: a question, optionally an edited copy of the agent's config and
// skill files, optionally the two ground-truth fields, and this platform's own
// downstream services.
//
// Only the question is required, and that is the point of the whole tab.
//
// **The panels open in a sheet, not inline.** They used to expand in place,
// above the three columns that are the actual working surface — which cost those
// columns up to 400px, and for the config tree the cap was lifted entirely, so
// one panel could push the trace and the send button off the bottom of the
// window. Editing a skill file needs room; a trace needs more. A sheet spends
// width, which a desktop has, rather than height, which this screen does not.
//
// **The composer collapses once an attempt exists.** Before you send, this is
// the screen; after you send, the trace is, and a composer holding 170px open to
// re-state a question you just asked is 170px the trace should have. The
// collapsed bar still carries the workspace edit counts, because those are the
// only thing on screen saying the next question will not run against the agent's
// own workspace.
//
// `connected` gates exactly the two things that need an agent — asking a
// question, and the two panels whose content is *read from* that agent. The
// ground truths and the LLM/Langfuse settings stay open: they are local text and
// downstream endpoints, and there is no reason to make someone connect before
// they are allowed to think about the question they want to ask.
const PANELS = {
  config: {
    label: "Agent config",
    title: "Agent config",
    subtitle: "The agent's own config.json, applied to this question only. Nothing is written back.",
    icon: <IconGear size={13} />,
    needsAgent: true,
  },
  skills: {
    label: "Skill files",
    title: "Skill files",
    subtitle: "The agent's SKILL.md and reference files, applied to this question only.",
    icon: <IconFileText size={13} />,
    needsAgent: true,
  },
  truth: {
    label: "Expected answer & process",
    title: "Expected answer & process",
    subtitle: "Both optional. An expected answer turns grading on; an expected process turns diagnosis on.",
    icon: <IconTarget size={13} />,
  },
  endpoints: {
    label: "LLM & Langfuse",
    title: "LLM & Langfuse",
    subtitle: "What this platform uses to grade the answer and read the trace — not the agent's own settings.",
    icon: <IconBeaker size={13} />,
  },
};

export default function PlaygroundComposer({
  draft, setDraft, form, set, setNum, secrets, setSecrets, impls, onSend, busy,
  connected = true,
  workspace, workspaceEdit, onWorkspaceEdit, workspaceLoading, workspaceError,
  onReloadWorkspace,
  open = true, onOpenChange, lastQuestion, status,
}) {
  // null | "config" | "skills" | "truth" | "endpoints"
  const [panel, setPanel] = useState(null);

  const field = (key) => (e) => setDraft({ ...draft, [key]: e.target.value });
  const canSend = connected && draft.question.trim().length > 0 && !busy;

  // Counts stay on the toolbar so an edit is visible from the closed state.
  // Otherwise closing a panel hides the fact that the next question will not run
  // against the agent's own workspace.
  const configCount =
    workspace && workspaceEdit
      ? flattenLeaves(diffConfig(workspace.config, workspaceEdit.config) || {}).length
      : 0;
  const fileCount =
    workspace && workspaceEdit ? editedFiles(workspace.skills, workspaceEdit.skills).length : 0;
  const truthSet = Boolean(draft.ground_truth_response || draft.ground_truth_reasoning);
  const edits = configCount + fileCount;

  if (!open) {
    return (
      <div className="composer composer-collapsed">
        <Button
          variant="primary"
          icon={<IconSend size={14} />}
          onClick={() => onOpenChange?.(true)}
        >
          Ask another question
        </Button>
        {lastQuestion && (
          <span className="composer-last" title={lastQuestion}>
            Last asked: {lastQuestion}
          </span>
        )}
        {/* Survives the collapse on purpose: this is the only thing saying the
            next question will not run against the agent's own workspace. */}
        {edits > 0 && (
          <Badge tone="warning" title="The next question will run against your edited workspace">
            {edits} workspace edit{edits === 1 ? "" : "s"}
          </Badge>
        )}
        {/* The open attempt's progress rides in the same row rather than on one
            of its own: both are describing the attempt on screen below. */}
        {status}
      </div>
    );
  }

  return (
    <div className="composer">
      <div className="field">
        <label htmlFor="pg-question">Question</label>
        <textarea
          id="pg-question"
          className="composer-question"
          value={draft.question}
          disabled={!connected}
          placeholder={
            connected
              ? "Ask the agent one question…"
              : "Connect to an agent above, then ask it one question."
          }
          onChange={field("question")}
          onKeyDown={(e) => {
            // Enter alone inserts a newline: questions are often multi-line, and
            // losing a half-typed one to a stray Enter would be worse than
            // needing a modifier.
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && canSend) onSend();
          }}
        />
      </div>

      <div className="composer-toggles">
        <Toggle
          name="config"
          active={panel === "config"}
          count={configCount}
          disabled={!connected}
          onClick={setPanel}
          hint={connected ? undefined : "Connect to an agent to read its config"}
        />
        <Toggle
          name="skills"
          active={panel === "skills"}
          count={fileCount}
          disabled={!connected}
          onClick={setPanel}
          hint={connected ? undefined : "Connect to an agent to read its skill files"}
        />
        <Toggle name="truth" active={panel === "truth"} flag={truthSet ? "set" : null} onClick={setPanel} />
        <Toggle name="endpoints" active={panel === "endpoints"} onClick={setPanel} />
        <div className="grow" />
        <Button
          variant="primary"
          icon={<IconSend size={14} />}
          disabled={!canSend}
          loading={busy}
          onClick={onSend}
        >
          {busy ? "Sending…" : "Ask the agent"}
        </Button>
      </div>

      {/* Loud even from the closed state: a workspace nobody can read is the
          reason someone retypes a skill from memory and tests the wrong text.
          Suppressed while disconnected, where the connection bar is already
          showing the same reason in the place you would act on it — one failure
          reported twice reads as two failures. */}
      {connected && workspaceError && (
        <div className="hint error-text composer-alert">
          <IconAlert size={13} /> Could not read the agent's workspace — open{" "}
          <button className="ui-btn ui-btn-link" onClick={() => setPanel("config")}>
            Agent config
          </button>{" "}
          for the reason.
        </div>
      )}

      <Drawer
        open={panel !== null}
        title={panel ? PANELS[panel].title : ""}
        subtitle={panel ? PANELS[panel].subtitle : ""}
        onClose={() => setPanel(null)}
        width={panel === "config" || panel === "skills" ? 720 : 560}
      >
        {/* All four render whenever any has been opened, so switching between
            them — and closing the sheet — never discards what is half-typed in
            another. WorkspaceEditor in particular holds not-yet-valid JSON. */}
        <div hidden={panel !== "config" && panel !== "skills"}>
          <WorkspaceEditor
            tab={panel === "skills" ? "skills" : "config"}
            snapshot={workspace}
            edit={workspaceEdit}
            onChange={onWorkspaceEdit}
            loading={workspaceLoading}
            error={workspaceError}
            onReload={onReloadWorkspace}
            fakeSeam={impls.workspace === "fake"}
          />
        </div>

        <div hidden={panel !== "truth"} className="composer-truth">
          <div className="field">
            <label>Expected answer — optional</label>
            <textarea
              value={draft.ground_truth_response || ""}
              placeholder="Leave blank to skip grading."
              onChange={field("ground_truth_response")}
            />
            <div className="hint">Given one, the answer is graded against it.</div>
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
              not a verdict.
            </div>
          </div>

          {/* Freely editable here, unlike on an eval set where only the owner
              may touch it. An attempt belongs to no set, so there is no shared
              pass rate to keep comparable — trying a prompt out is exactly what
              this screen is for. What it must not do is grade with a criterion
              the developer doesn't know they inherited, hence the provenance
              line: a question carried over from a run brings that run's frozen
              grading prompt with it. */}
          <details className="field">
            <summary className="ui-summary-link">
              Grading prompt —{" "}
              {form?.judge_prompt_fingerprint
                ? `carried over from the run you came from (${form.judge_prompt_fingerprint})`
                : "the built-in default"}
            </summary>
            <div className="hint" style={{ margin: "8px 0" }}>
              Applies to this attempt only, and is never written back to any eval
              set. Leave a box empty to use the built-in prompt. The user prompt
              needs <code>{"{question}"}</code>, <code>{"{ground_truth}"}</code>{" "}
              and <code>{"{agent_response}"}</code>.
            </div>
            <label>System</label>
            <textarea
              rows={8}
              spellCheck={false}
              value={form?.judge_system_prompt || ""}
              placeholder="Blank — using the built-in system prompt."
              onChange={(e) => set("judge_system_prompt", e.target.value)}
            />
            <label style={{ marginTop: 8 }}>User</label>
            <textarea
              rows={6}
              spellCheck={false}
              value={form?.judge_user_prompt || ""}
              placeholder="Blank — using the built-in user prompt."
              onChange={(e) => set("judge_user_prompt", e.target.value)}
            />
          </details>
        </div>

        <div hidden={panel !== "endpoints"}>
          <RunConfigFields
            form={form}
            set={set}
            setNum={setNum}
            secrets={secrets}
            setSecrets={setSecrets}
            impls={impls}
            showAgent={false}
            showConcurrency={false}
          />
          <div className="hint">
            These stay for the rest of this browser session, so keys are typed
            once, and they are never sent back to the browser.
          </div>
        </div>
      </Drawer>
    </div>
  );
}

// One panel toggle. `count` is an edit count — amber, because it means the next
// question will differ from what the agent server itself is configured with.
// `flag` is a plain state word.
function Toggle({ name, active, count, flag, disabled, onClick, hint }) {
  const panel = PANELS[name];
  return (
    <button
      className={active ? "active" : ""}
      title={hint || panel.subtitle}
      disabled={disabled}
      onClick={() => onClick(active ? null : name)}
    >
      {panel.icon}
      {panel.label}
      {count > 0 && <span className="count edited">{count}</span>}
      {flag && <span className="count">{flag}</span>}
    </button>
  );
}
