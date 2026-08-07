import React, { useState } from "react";
import RunConfigFields from "./RunConfigFields.jsx";
import WorkspaceEditor from "./WorkspaceEditor.jsx";
import { diffConfig, editedFiles, flattenLeaves } from "../workspace_util.js";
import { IconAlert, IconBeaker, IconFileText, IconGear, IconSend, IconTarget } from "./icons.jsx";
import Button from "./ui/Button.jsx";

// What gets sent: a question, optionally an edited copy of the agent's config and
// skill files, optionally the two ground-truth fields, and this platform's own
// downstream services.
//
// Only the question is required, and that is the point of the whole tab.
// Everything else is a panel behind one toolbar, and **only one panel is open at
// a time**, for two reasons this layout learned the hard way:
//
//   * The composer sits above the three columns that are the actual working
//     surface. Two panels open at once pushed the columns — and the send button
//     — off the bottom of the window.
//   * The panels used to be two rows of identical-looking buttons: the agent's
//     workspace on one, this platform's settings on the other, one labelled
//     "Config" and the other "Settings". Two words for two unrelated things, in
//     the same visual register. One row of peers, each naming what it actually
//     edits, removes the guess.
//
// The fourth panel no longer holds the agent's own URL and timeout: those moved
// up to the connection bar, because they are the premise of this composer rather
// than one more setting on it (see AgentConnectionBar). What is left here is the
// platform's downstream services, which is what the button now says.
//
// `connected` gates exactly the two things that need an agent — asking a
// question, and the two panels whose content is *read from* that agent. The
// ground truths and the LLM/Langfuse settings stay open: they are local text and
// downstream endpoints, and there is no reason to make someone connect before
// they are allowed to think about the question they want to ask.
export default function PlaygroundComposer({
  draft, setDraft, form, set, setNum, secrets, setSecrets, impls, onSend, busy,
  connected = true,
  workspace, workspaceEdit, onWorkspaceEdit, workspaceLoading, workspaceError,
  onReloadWorkspace,
}) {
  // null | "config" | "skills" | "truth" | "endpoints"
  const [panel, setPanel] = useState(null);
  const toggle = (name) => setPanel((p) => (p === name ? null : name));

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

  return (
    <div className="composer">
      <div className="field">
        <label>Question</label>
        <textarea
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
          label="Agent config"
          title={
            connected
              ? "The agent's own config.json — applied to this question only"
              : "Connect to an agent to read its config"
          }
          icon={<IconGear size={13} />}
          active={panel === "config"}
          count={configCount}
          disabled={!connected}
          onClick={() => toggle("config")}
        />
        <Toggle
          label="Skill files"
          title={
            connected
              ? "The agent's SKILL.md and reference files — applied to this question only"
              : "Connect to an agent to read its skill files"
          }
          icon={<IconFileText size={13} />}
          active={panel === "skills"}
          count={fileCount}
          disabled={!connected}
          onClick={() => toggle("skills")}
        />
        <Toggle
          label="Expected answer & process"
          title="Optional — an expected answer turns judging on, an expected process turns diagnosis on"
          icon={<IconTarget size={13} />}
          active={panel === "truth"}
          flag={truthSet ? "set" : null}
          onClick={() => toggle("truth")}
        />
        <Toggle
          label="LLM & Langfuse"
          title="Which LLM and Langfuse this platform uses to judge, diagnose and read traces"
          icon={<IconBeaker size={13} />}
          active={panel === "endpoints"}
          onClick={() => toggle("endpoints")}
        />
        <div className="grow" />
        <Button variant="primary" icon={<IconSend size={14} />} disabled={!canSend} loading={busy} onClick={onSend}>
          {busy ? "Sending…" : "Ask the agent"}
        </Button>
      </div>

      {/* Loud even from the closed state: a workspace nobody can read is the
          reason someone retypes a skill from memory and tests the wrong text.
          Suppressed while disconnected, where the connection bar is already
          showing the same reason in the place you would act on it — one failure
          reported twice reads as two failures. */}
      {connected && workspaceError && panel !== "config" && panel !== "skills" && (
        <div className="hint error-text composer-alert">
          <IconAlert size={13} /> Could not read the agent's workspace — open{" "}
          <button className="ui-btn ui-btn-link" onClick={() => setPanel("config")}>
            Agent config
          </button>{" "}
          for the reason.
        </div>
      )}

      {(panel === "config" || panel === "skills") && (
        <div className="composer-panel">
          <WorkspaceEditor
            tab={panel}
            snapshot={workspace}
            edit={workspaceEdit}
            onChange={onWorkspaceEdit}
            loading={workspaceLoading}
            error={workspaceError}
            onReload={onReloadWorkspace}
            fakeSeam={impls.workspace === "fake"}
          />
        </div>
      )}

      {panel === "truth" && (
        <div className="composer-panel composer-truth">
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
              not a verdict.
            </div>
          </div>

          {/* Freely editable here, unlike on an eval set where only the owner
              may touch it. An attempt belongs to no set, so there is no shared
              pass rate to keep comparable — trying a prompt out is exactly what
              this screen is for. What it must not do is grade with a criterion
              the developer doesn't know they inherited, hence the provenance
              line: a question carried over from a run brings that run's frozen
              judge prompt with it. */}
          <details className="field">
            <summary className="ui-summary-link">
              Judge prompt —{" "}
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
              placeholder="Blank — using the built-in judge system prompt."
              onChange={(e) => set("judge_system_prompt", e.target.value)}
            />
            <label style={{ marginTop: 8 }}>User</label>
            <textarea
              rows={6}
              spellCheck={false}
              value={form?.judge_user_prompt || ""}
              placeholder="Blank — using the built-in judge user prompt."
              onChange={(e) => set("judge_user_prompt", e.target.value)}
            />
          </details>
        </div>
      )}

      {panel === "endpoints" && (
        <div className="composer-panel">
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
            What this platform uses to grade the answer and read the trace — not
            the agent's own settings, and not the agent itself, which is the bar
            above. These stay for the rest of this browser session, so keys are
            typed once, and they are never sent back to the browser.
          </div>
        </div>
      )}
    </div>
  );
}

// One panel toggle. `count` is an edit count — amber, because it means the next
// question will differ from what the agent server itself is configured with.
// `flag` is a plain state word.
function Toggle({ label, title, icon, active, count, flag, disabled, onClick }) {
  return (
    <button
      className={active ? "active" : ""}
      title={title}
      disabled={disabled}
      onClick={onClick}
    >
      {icon}
      {label}
      {count > 0 && <span className="count edited">{count}</span>}
      {flag && <span className="count">{flag}</span>}
    </button>
  );
}
