import React from "react";
import { IconCopy, IconPlus, IconStop, IconTrash } from "./icons.jsx";
import { overrideCounts } from "../workspace_util.js";

// Left column of the playground: this session's attempts, newest first.
//
// The iteration loop lives here. "Clone" is the important control — it puts an
// attempt's question, workspace edits and settings back in the composer so the next
// attempt differs by exactly the one thing being tested, which is the only way a
// before/after comparison means anything, given how non-deterministic the model is.
//
// Attempts are held in the backend's memory, so this list empties on a
// backend restart. The footer says so rather than letting an empty list look like
// a bug.

// Counts, not names: what matters in a list row is that this attempt differed
// from the agent's own workspace. The paths themselves are the tooltip.
function overrideLabel(a) {
  const { configs, files } = overrideCounts(a);
  const parts = [];
  if (configs) parts.push(`${configs} config`);
  if (files) parts.push(`${files} file${files === 1 ? "" : "s"}`);
  return parts.length ? `edited: ${parts.join(", ")}` : "workspace override";
}

function relative(iso) {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return `${Math.floor(seconds)}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  return new Date(iso).toLocaleTimeString();
}

function dotClass(a) {
  if (a.status === "failed") return "failed";
  if (a.status === "cancelled") return "cancelled";
  if (a.status === "running") return a.phase === "pending" ? "pending" : "answered";
  if (a.verdict === "correct") return "correct";
  if (a.verdict === "incorrect") return "incorrect";
  return "answered"; // finished but never graded — no expected answer was given
}

function note(a) {
  if (a.status === "failed") return "failed";
  if (a.status === "cancelled") return "stopped";
  if (a.status === "running") {
    return a.phase === "pending" ? "asking the agent…" : `${a.phase}…`;
  }
  return a.verdict || "not judged";
}

export default function AttemptList({
  attempts, activeId, onPick, onClone, onCancel, onDelete,
  shortlistedIds, onShortlist,
}) {
  return (
    <div className="col">
      <div className="ui-card-head is-sticky">
        <h4>Attempts</h4>
        <span className="hint">{attempts.length}</span>
      </div>

      {attempts.map((a) => (
        <div
          key={a.id}
          className={`qitem ${a.phase} ${activeId === a.id ? "active" : ""}`}
          onClick={() => onPick(a)}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              onPick(a);
            }
          }}
        >
          <span className={`dot ${dotClass(a)}`} />
          <div className="grow">
            <div className="qtext">{a.question.slice(0, 60)}</div>
            <div className="qid">
              {relative(a.created_at)} · <span className={`qphase ${a.phase}`}>{note(a)}</span>
              {a.agent_latency_ms != null && ` · ${(a.agent_latency_ms / 1000).toFixed(1)}s`}
            </div>
            <div className="attempt-tags">
              {a.workspace_overridden ? (
                <span
                  className="ui-badge ui-badge-neutral"
                  title={[...(a.config_overrides || []), ...(a.edited_skill_files || [])]
                    .join("\n") || "A workspace override was sent with this call"}
                >
                  {overrideLabel(a)}
                </span>
              ) : (
                <span className="ui-badge ui-badge-neutral">agent's own workspace</span>
              )}
              {!a.has_expected_answer && <span className="ui-badge ui-badge-neutral">not judged</span>}
            </div>
            {a.error_message && (
              <div className="qerror" title={a.error_message}>
                {a.error_message.slice(0, 80)}
              </div>
            )}
          </div>
          <div className="attempt-actions">
            {/* Only a finished attempt has an answer to promote, and the
                shortlist copies that answer in as the starting ground truth. */}
            <button
              className={shortlistedIds?.has(a.id) ? "active" : ""}
              disabled={a.status === "running" || shortlistedIds?.has(a.id)}
              title={
                shortlistedIds?.has(a.id)
                  ? "Already shortlisted"
                  : "Shortlist this question for a new eval set"
              }
              onClick={(e) => {
                e.stopPropagation();
                onShortlist(a);
              }}
            >
              <IconPlus size={13} />
            </button>
            <button
              title="Copy this attempt's question, workspace edits and settings into the composer"
              onClick={(e) => {
                e.stopPropagation();
                onClone(a);
              }}
            >
              <IconCopy size={13} />
            </button>
            {a.status === "running" ? (
              <button
                className="ui-btn ui-btn-danger ui-btn-sm"
                title="Stop this attempt"
                onClick={(e) => {
                  e.stopPropagation();
                  onCancel(a);
                }}
              >
                <IconStop size={13} />
              </button>
            ) : (
              <button
                title="Forget this attempt"
                onClick={(e) => {
                  e.stopPropagation();
                  onDelete(a);
                }}
              >
                <IconTrash size={13} />
              </button>
            )}
          </div>
        </div>
      ))}

      {attempts.length === 0 && (
        <div className="ui-empty-inline">
          No attempts yet. Ask the agent something above.
        </div>
      )}
      {attempts.length > 0 && (
        <div className="attempt-footnote">
          Attempts live in the backend's memory — restarting it clears this list.
        </div>
      )}
    </div>
  );
}
