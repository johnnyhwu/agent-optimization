import React, { useLayoutEffect, useRef, useState } from "react";
import RunConfigFields from "./RunConfigFields.jsx";
import WorkspaceEditor from "./WorkspaceEditor.jsx";
import Badge from "./ui/Badge.jsx";
import Button from "./ui/Button.jsx";
import PanelDialog from "./ui/PanelDialog.jsx";
import { editedFiles } from "../workspace_util.js";
import {
  IconAlert, IconBeaker, IconFileText, IconSend, IconTarget,
} from "./icons.jsx";

// What gets sent: a question, optionally an edited copy of the agent's config and
// skill files, optionally the two ground-truth fields, and this platform's own
// downstream services.
//
// Only the question is required, and that is the point of the whole tab.
//
// **The panels open in a dialog, not inline.** They used to expand in place,
// above the three columns that are the actual working surface — which cost those
// columns up to 400px, and for the config tree the cap was lifted entirely, so
// one panel could push the trace and the send button off the bottom of the
// window. See PanelDialog for why the dialog is centered rather than a sheet.
//
// **The composer never collapses.** It used to, on the theory that after a send
// the trace is the screen and a composer restating the question you just asked
// is 170px the trace should have. The theory was right about the height and
// wrong about the trade: asking a second question is the single most common
// thing anyone does here, and it cost a click on "Ask another question" every
// time. The height is bought back instead — the question box sizes itself to its
// content rather than sitting at a fixed 72px, and the attempt's progress rides
// in the button row instead of on a row of its own.
//
// **The button row is a readout, not a tab strip.** Each toggle carries the
// state of what it holds — how many skill files are overridden, whether a
// ground truth is set — so the row answers "what will the next question
// actually run with" without opening anything. That is why the edit counts are
// amber and stay on screen whatever is open.
//
// `connected` gates exactly the two things that need an agent — asking a
// question, and the panel whose content is *read from* that agent. The ground
// truths and the LLM/Langfuse settings stay open: they are local text and
// downstream endpoints, and there is no reason to make someone connect before
// they are allowed to think about the question they want to ask.
const PANELS = {
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
  status,
}) {
  // null | "skills" | "truth" | "endpoints"
  const [panel, setPanel] = useState(null);
  const questionRef = useRef(null);

  const field = (key) => (e) => setDraft({ ...draft, [key]: e.target.value });
  const canSend = connected && draft.question.trim().length > 0 && !busy;

  // Grow with the question rather than sit at a fixed height. Two rows is enough
  // for most questions and less than the old fixed box, which is where the space
  // for keeping this open permanently comes from; past the cap it scrolls, so a
  // pasted essay can never push the trace off the screen. No transition — the
  // box should track the text, not animate behind it.
  useLayoutEffect(() => {
    const el = questionRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [draft.question]);

  // Sending from inside a panel closes it: the answer and the trace it is about
  // to produce are behind the dialog, and leaving it open would hide the thing
  // the button was pressed to see.
  const sendAndClose = () => {
    setPanel(null);
    onSend();
  };

  // The count stays on the toolbar so an edit is visible from the closed state.
  // Otherwise closing the panel hides the fact that the next question will not
  // run against the agent's own skill files.
  const edits =
    workspace && workspaceEdit ? editedFiles(workspace.skills, workspaceEdit.skills).length : 0;
  const truthSet = Boolean(draft.ground_truth_response || draft.ground_truth_reasoning);

  return (
    <div className="composer">
      {/* No visible label: the placeholder already says what the box is for, and
          a heading above every question would be a word that earns nothing. */}
      <textarea
        id="pg-question"
        ref={questionRef}
        className="composer-question"
        aria-label="Question"
        rows={2}
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

      <div className="composer-toggles">
        <Toggle
          name="skills"
          active={panel === "skills"}
          count={edits}
          disabled={!connected}
          onClick={setPanel}
          hint={connected ? undefined : "Connect to an agent to read its skill files"}
        />
        <Toggle name="truth" active={panel === "truth"} flag={truthSet ? "set" : null} onClick={setPanel} />
        <Toggle name="endpoints" active={panel === "endpoints"} onClick={setPanel} />
        <div className="grow" />
        {/* The open attempt's progress rides in this row rather than on one of
            its own — a second row of chrome is exactly the height the composer
            just stopped collapsing to save. */}
        {status}
        {edits > 0 && (
          <Badge tone="warning" title="The next question will run against your edited workspace">
            {edits} skill file edit{edits === 1 ? "" : "s"}
          </Badge>
        )}
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
          <IconAlert size={13} /> Could not read the agent's skill files — open{" "}
          <button className="ui-btn ui-btn-link" onClick={() => setPanel("skills")}>
            Skill files
          </button>{" "}
          for the reason.
        </div>
      )}

      <PanelDialog
        open={panel !== null}
        title={panel ? PANELS[panel].title : ""}
        subtitle={panel ? PANELS[panel].subtitle : ""}
        onClose={() => setPanel(null)}
        // The two-pane skills panel needs room for a file list beside a file;
        // the form panels do not.
        width={panel === "skills" ? 1080 : 880}
        footer={
          <>
            {/* Says what is missing rather than leaving a dead button with no
                account of itself. */}
            {connected && !draft.question.trim() && (
              <span className="hint ui-panel-foot-hint">
                Type a question to send.
              </span>
            )}
            <div className="grow" />
            <Button variant="secondary" onClick={() => setPanel(null)}>
              Close
            </Button>
            {/* The same words as the composer's own button, because it is the
                same action — settings entered here take effect on the next
                question either way, so there is no reason to leave the dialog
                to send one. */}
            <Button
              variant="primary"
              icon={<IconSend size={14} />}
              disabled={!canSend}
              loading={busy}
              onClick={sendAndClose}
            >
              {busy ? "Sending…" : "Ask the agent"}
            </Button>
          </>
        }
      >
        {/* All three render whenever any has been opened, so switching between
            them — and closing the dialog — never discards what is half-typed in
            another. */}
        <div className="panel-fill" hidden={panel !== "skills"}>
          <WorkspaceEditor
            snapshot={workspace}
            edit={workspaceEdit}
            onChange={onWorkspaceEdit}
            loading={workspaceLoading}
            error={workspaceError}
            onReload={onReloadWorkspace}
            fakeSeam={impls.workspace === "fake"}
          />
        </div>

        <div hidden={panel !== "truth"} className="panel-fill composer-truth">
          {/* Prose, so these are set in the body face rather than the mono the
              rest of `.field textarea` uses — an expected answer is written, not
              typed like config. */}
          <div className="field field-prose">
            <label>Expected answer — optional</label>
            <textarea
              value={draft.ground_truth_response || ""}
              placeholder="Leave blank to skip grading."
              onChange={field("ground_truth_response")}
            />
            <div className="hint">Given one, the answer is graded against it.</div>
          </div>
          <div className="field field-prose">
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
          <details className="field composer-judge">
            <summary className="ui-summary-link">
              Grading prompt —{" "}
              {form?.judge_prompt_fingerprint
                ? `carried over from the run you came from (${form.judge_prompt_fingerprint})`
                : "the built-in default"}
            </summary>
            <div className="hint composer-judge-note">
              Applies to this attempt only, and is never written back to any eval
              set. Leave a box empty to use the built-in prompt. The user prompt
              needs <code>{"{question}"}</code>, <code>{"{ground_truth}"}</code>{" "}
              and <code>{"{agent_response}"}</code>.
            </div>
            {/* Side by side rather than stacked: they are one prompt in two
                parts, and the dialog is wide enough to read them as one. */}
            <div className="composer-judge-pair">
              <div className="field field-prose">
                <label>System</label>
                <textarea
                  spellCheck={false}
                  value={form?.judge_system_prompt || ""}
                  placeholder="Blank — using the built-in system prompt."
                  onChange={(e) => set("judge_system_prompt", e.target.value)}
                />
              </div>
              <div className="field field-prose">
                <label>User</label>
                <textarea
                  spellCheck={false}
                  value={form?.judge_user_prompt || ""}
                  placeholder="Blank — using the built-in user prompt."
                  onChange={(e) => set("judge_user_prompt", e.target.value)}
                />
              </div>
            </div>
          </details>
        </div>

        <div hidden={panel !== "endpoints"} className="panel-fill composer-endpoints">
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
      </PanelDialog>
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
