import React from "react";
import { IconStop } from "./icons.jsx";

// Where a run stands, above the three columns. Counts come from the question
// rows themselves rather than a separate progress feed, so the bar and the left
// column can never disagree about how far along the run is.
//
// Segments mirror the left column's colours: judged-correct, judged-incorrect,
// answered-but-unjudged, then everything not started yet.
export default function RunStatusBar({ results, running, cancelling, onCancel, canCancel }) {
  const total = results.length;
  if (!total) return null;

  const count = (fn) => results.filter(fn).length;
  const correct = count((r) => r.phase === "judged" && r.verdict === "correct");
  const incorrect = count((r) => r.phase === "judged" && r.verdict === "incorrect");
  const failed = count((r) => r.phase === "failed" || r.phase === "cancelled");
  const answered = count((r) => r.phase === "answered");
  const judged = correct + incorrect;
  const responded = judged + answered + failed;
  const pending = total - responded;

  const pct = (n) => Math.round((n / total) * 100);
  const seg = (n) => ({ width: `${(n / total) * 100}%` });

  return (
    <div className="runstatus">
      <div className="runstatus-head">
        <div className="runstatus-nums">
          <span>
            <strong>{judged}</strong>/{total} judged
            <span className="muted"> ({pct(judged)}%)</span>
          </span>
          <span className="sep">·</span>
          <span>
            <strong>{responded}</strong>/{total} answered
            <span className="muted"> ({pct(responded)}%)</span>
          </span>
          <span className="sep">·</span>
          <span className="muted">
            {pending} not started ({pct(pending)}%)
          </span>
          {failed > 0 && (
            <>
              <span className="sep">·</span>
              <span className="failedcount">{failed} failed/stopped</span>
            </>
          )}
        </div>
        {running && canCancel && (
          <button className="danger" onClick={onCancel} disabled={cancelling}>
            <IconStop size={13} /> {cancelling ? "Stopping…" : "Stop run"}
          </button>
        )}
      </div>
      <div className="stackbar" role="img"
           aria-label={`${judged} of ${total} judged, ${responded} answered`}>
        <div className="s correct" style={seg(correct)} />
        <div className="s incorrect" style={seg(incorrect)} />
        <div className="s failed" style={seg(failed)} />
        <div className="s answered" style={seg(answered)} />
        <div className="s pending" style={seg(pending)} />
      </div>
    </div>
  );
}
