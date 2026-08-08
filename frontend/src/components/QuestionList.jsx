import React from "react";
import Card, { CardHeader } from "./ui/Card.jsx";
import { InlineEmpty } from "./ui/EmptyState.jsx";
import { SegmentedControl } from "./ui/Toolbar.jsx";
import ElapsedTimer from "./ElapsedTimer.jsx";

// Left column. Two jobs:
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
  // not the network's. The judge answered in a shape we could not read, which
  // points at the eval set's grading criteria — the one thing an owner can go
  // and fix. It is still not a pass, and still in the pass rate's denominator.
  judge_invalid: "not graded",
  cancelled: "stopped",
};

export default function QuestionList({ results, activeId, filter, setFilter, onPick }) {
  const wrongCount = results.filter((r) => r.is_incorrect).length;
  const shown = filter === "wrong" ? results.filter((r) => r.is_incorrect) : results;

  return (
    <Card padded={false} className="col">
      <CardHeader
        title="Questions"
        sticky
        actions={
          <SegmentedControl
            value={filter}
            onChange={setFilter}
            size="sm"
            ariaLabel="Filter questions"
            options={[
              { value: "all", label: "All", count: results.length },
              { value: "wrong", label: "Wrong", count: wrongCount },
            ]}
          />
        }
      />

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
            role="button"
            tabIndex={0}
            onClick={() => onPick(r)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onPick(r);
              }
            }}
          >
            {/* The dot is a second encoding of the word beside it, not the only
                one — the phase is always spelled out in `note`. */}
            <span className={`dot ${dot}`} aria-hidden="true" />
            <div className="grow">
              {/* Cut by CSS, not by JS. Slicing to 60 characters *and* then
                  ellipsising meant a wider column showed more empty space
                  rather than more question. */}
              <div className="qtext" title={r.question}>{r.question}</div>
              <div className="qid">
                {r.question_id} · <span className={`qphase ${r.phase}`}>{note}</span>
                {/* Counts up while the question is with the agent and settles
                    on the measured value. Rows from before `started_at` was
                    recorded show nothing rather than a fabricated duration. */}
                {(r.started_at || r.agent_latency_ms != null) && " · "}
                <ElapsedTimer startedAt={r.started_at} finalMs={r.agent_latency_ms} />
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
        <InlineEmpty>
          {filter === "wrong"
            ? "Every question in this selection passed."
            : "No questions."}
        </InlineEmpty>
      )}
      {shown.length > 0 && (
        // The same standing note the attempt list carries, for the same reason:
        // a settled time beside a row still reading "judging…" only makes sense
        // once you know the timer is the agent's.
        <div className="attempt-footnote">
          Times are the agent's — from sending the question to its answer.
          Grading and trace analysis are not counted.
        </div>
      )}
    </Card>
  );
}
