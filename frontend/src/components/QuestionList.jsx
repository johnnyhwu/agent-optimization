import React from "react";

// Left column (§6.13). Two jobs:
//
//  1. Filter. The old bare "only wrong" checkbox floated in the header; a
//     segmented control with live counts says what is being hidden as well as
//     how to unhide it.
//  2. Show a run's shape while it is still executing. Every question exists from
//     the start (the orchestrator creates all result rows up front), so a row
//     moves grey -> plain -> green/red rather than appearing out of nowhere.
//
// `phase` is computed by the backend so this list and the SSE stream agree; see
// services/aggregation.result_phase.

const PHASE_LABEL = {
  pending: "waiting",
  answered: "judging…",
  failed: "failed",
  // Separated from "failed" on purpose: this one is not the agent's fault and
  // not the network's. The judge answered in a shape we could not parse, which
  // points at the eval set's judge prompt — the one thing an owner can go and
  // fix. It is still not a pass, and still in the pass rate's denominator.
  judge_invalid: "not judged",
  cancelled: "stopped",
};

export default function QuestionList({ results, activeId, filter, setFilter, onPick }) {
  const wrongCount = results.filter((r) => r.is_incorrect).length;
  const shown = filter === "wrong" ? results.filter((r) => r.is_incorrect) : results;

  return (
    <div className="col">
      <div className="col-head">
        <h4>Questions</h4>
        <div className="segmented sm">
          <button
            className={filter === "all" ? "active" : ""}
            onClick={() => setFilter("all")}
          >
            All <span className="count">{results.length}</span>
          </button>
          <button
            className={filter === "wrong" ? "active" : ""}
            onClick={() => setFilter("wrong")}
          >
            Wrong <span className="count">{wrongCount}</span>
          </button>
        </div>
      </div>

      {shown.map((r) => {
        // Colour follows the phase; only a judged question is green or red.
        const dot =
          r.phase === "failed" || r.phase === "cancelled" || r.phase === "judge_invalid"
            ? r.phase
            : r.phase === "judged"
            ? r.is_incorrect
              ? "incorrect"
              : "correct"
            : r.phase; // pending | answered
        const note = PHASE_LABEL[r.phase] || (r.is_incorrect ? "incorrect" : "correct");
        return (
          <div
            key={r.id}
            className={`qitem ${r.phase} ${activeId === r.id ? "active" : ""}`}
            onClick={() => onPick(r)}
          >
            <span className={`dot ${dot}`} />
            <div className="grow">
              <div className="qtext">{r.question.slice(0, 60)}</div>
              <div className="qid">
                {r.question_id} · <span className={`qphase ${r.phase}`}>{note}</span>
              </div>
              {/* A bare "failed" says nothing once the agent is a real service. */}
              {(r.phase === "failed" || r.phase === "cancelled" || r.phase === "judge_invalid") &&
                r.error_message && (
                <div className="qerror" title={r.error_message}>
                  {r.error_message.slice(0, 80)}
                </div>
              )}
            </div>
          </div>
        );
      })}
      {shown.length === 0 && (
        <div className="notflagged">
          {filter === "wrong" ? "No incorrect questions in this selection." : "No questions."}
        </div>
      )}
    </div>
  );
}
