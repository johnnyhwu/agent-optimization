import React from "react";
import { IconButton } from "./ui/Button.jsx";
import { IconClock, IconCopy, IconPanelLeft, IconPlus, IconStop, IconTrash } from "./icons.jsx";
import { overrideCounts } from "../workspace_util.js";
import ElapsedTimer from "./ElapsedTimer.jsx";
import { isTimeout } from "../failure.js";
import { plural } from "../plural.js";
import { relativeStamp } from "../timestamp.js";

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

// A count, not names: what matters in a list row is that this attempt differed
// from the agent's own skill files. The paths themselves are the tooltip.
function overrideLabel(a) {
  const { files } = overrideCounts(a);
  return files ? `edited: ${files} file${files === 1 ? "" : "s"}` : "skill override";
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
  shortlistedIds, onShortlist, collapsed = false, onToggleCollapsed,
}) {
  // Collapsed, the list keeps only what it needs to be navigable: a dot per
  // attempt in the order they were made, and which one is open. Picking between
  // attempts and reading a trace are different tasks, and the second wants the
  // 320px back.
  if (collapsed) {
    return (
      <div className="col attempts-rail">
        <div className="attempts-rail-head">
          <IconButton
            label="Expand the attempt list"
            icon={<IconPanelLeft size={15} />}
            onClick={() => onToggleCollapsed?.(false)}
          />
          <span className="attempts-rail-count">{attempts.length}</span>
        </div>
        {attempts.map((a, i) => (
          <button
            key={a.id}
            className={`attempts-rail-item${activeId === a.id ? " active" : ""}`}
            title={a.question}
            aria-label={`Attempt ${attempts.length - i}: ${a.question}`}
            aria-current={activeId === a.id ? "true" : undefined}
            onClick={() => onPick(a)}
          >
            <span className={`dot ${dotClass(a)}`} />
          </button>
        ))}
      </div>
    );
  }

  return (
    <div className="col">
      <div className="ui-card-head is-sticky">
        <h4>Attempts</h4>
        <div className="ui-card-head-actions">
          <span className="hint">{attempts.length}</span>
          <IconButton
            label="Collapse the attempt list"
            icon={<IconPanelLeft size={15} />}
            onClick={() => onToggleCollapsed?.(true)}
          />
        </div>
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
            {/* Two lines, elided by the column's width. It used to be cut at 60
                characters *and* ellipsed, with no title — so a long question was
                unreadable here and nowhere else showed it either. The middle
                column carries the full text now; this only has to be enough to
                tell two attempts apart. */}
            <div className="qtext" title={a.question}>{a.question}</div>
            <div className="qid">
              {relativeStamp(a.created_at)} · <span className={`qphase ${a.phase}`}>{note(a)}</span>
              {/* One slot for the agent's time, counting up while the question
                  is out and settling on the measured value when it lands — so
                  "how long has this been going" and "how long did it take" are
                  the same number in the same place, rather than a duration that
                  appears from nowhere at the end. */}
              {(a.agent_started_at || a.agent_latency_ms != null) && " · "}
              <ElapsedTimer
                startedAt={a.agent_started_at}
                finalMs={a.agent_latency_ms}
                // A stopped or failed attempt is not still counting, even though
                // it has a start time and no measured duration — that is exactly
                // what "the agent never answered" looks like.
                running={a.status === "running"}
              />
              {/* What the answer cost, beside how long it took — the same pair,
                  in the same order, as a run's question row (QuestionList).
                  These two lists are the same design and were showing different
                  facts. Absent rather than zero when there is no trace to count
                  from: "no calls" is a claim, and it would be a false one. */}
              {a.llm_call_count != null && (
                <span title="Model calls the agent made answering this question">
                  {" · "}
                  {plural(a.llm_call_count, "call")}
                </span>
              )}
            </div>
            <div className="attempt-tags">
              {a.workspace_overridden ? (
                <span
                  className="ui-badge ui-badge-neutral"
                  title={(a.edited_skill_files || []).join("\n")
                    || "A skill override was sent with this call"}
                >
                  {overrideLabel(a)}
                </span>
              ) : (
                <span className="ui-badge ui-badge-neutral">agent's own skills</span>
              )}
              {!a.has_expected_answer && <span className="ui-badge ui-badge-neutral">not judged</span>}
            </div>
            {a.error_message && (
              isTimeout(a.failure_kind) ? (
                <div className="qerror is-timeout" title={a.error_message}>
                  <IconClock size={11} /> timed out
                </div>
              ) : (
                <div className="qerror" title={a.error_message}>
                  {a.error_message.slice(0, 80)}
                </div>
              )
            )}
          </div>
          <div className="attempt-actions">
            {/* Only a finished attempt has an answer to promote, and the
                shortlist copies that answer in as the starting ground truth. */}
            <button
              aria-label="Shortlist this attempt"
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
              aria-label="Clone this attempt into the composer"
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
                aria-label="Stop this attempt"
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
                aria-label="Forget this attempt"
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
          {/* Said once, standing, rather than left to the tooltip: a settled
              time sitting next to a row that still reads "judging…" is
              confusing precisely until you know the timer is the agent's. */}
          Times are the agent's — from sending the question to its answer.
          Grading and trace analysis are not counted.
          <br />
          Attempts live in the backend's memory — restarting it clears this list.
        </div>
      )}
    </div>
  );
}
