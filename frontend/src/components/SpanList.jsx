import React from "react";

// Middle column (§6.13): top overall_diagnosis + caveat banner, then the vertical
// span list with suspects marked (confidence high/med/low). Distinguishes
// "generating (retrying)" from "no trace" (§6.12 / §7.1 #5).
export default function SpanList({ trace, activeSpan, onPickSpan, canReDiagnose, onReDiagnose, reDiagnosing }) {
  if (!trace) return <div className="col"><h4>Trace</h4><div className="notflagged">Select a question.</div></div>;

  const suspectByIndex = {};
  (trace.analysis?.suspects || []).forEach((s) => (suspectByIndex[s.span_index] = s));

  return (
    <div className="col">
      <h4>Trace & diagnosis</h4>

      {/* What the agent answered, next to what it was graded against. With a real
          agent this is the first thing to read — the verdict alone doesn't say
          what went wrong. */}
      {(trace.agent_response || trace.ground_truth_response) && (
        <div className="answers">
          <div className="label">
            Agent answer
            {trace.verdict && <span className={`verdict ${trace.verdict}`}>{trace.verdict}</span>}
          </div>
          <pre>{trace.agent_response || "— (no answer recorded)"}</pre>
          <div className="label">Expected answer</div>
          <pre>{trace.ground_truth_response || "—"}</pre>
          {trace.judge_comment && (
            <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
              <strong>Judge:</strong> {trace.judge_comment}
            </div>
          )}
        </div>
      )}

      {trace.error_message && (
        <div className="banner error-banner">✕ This question failed: {trace.error_message}</div>
      )}

      {trace.trace_state === "generating" && (
        <div className="banner generating">
          ⏳ Trace is generating (Langfuse ingestion is async — retrying). This is not
          "no trace"; check back shortly.
        </div>
      )}
      {trace.trace_state === "no_trace" && (
        <div className="banner generating">No trace — the agent call failed for this question.</div>
      )}

      {trace.analysis && (
        <>
          <div className="banner diagnosis">
            <strong>Diagnosis (clue, not a verdict):</strong> {trace.analysis.overall_diagnosis}
            {canReDiagnose && (
              <div style={{ marginTop: 8 }}>
                <button onClick={onReDiagnose} disabled={reDiagnosing}>
                  {reDiagnosing ? "Re-diagnosing…" : "↻ Re-diagnose"}
                </button>
              </div>
            )}
          </div>
          {trace.analysis.caveat && (
            <div className="banner caveat">⚠ Caveat: {trace.analysis.caveat}</div>
          )}
        </>
      )}
      {trace.trace_state === "ready" && !trace.analysis && trace.verdict === "correct" && (
        <div className="banner diagnosis muted">Correct answer — no diagnosis generated.</div>
      )}

      {trace.spans.map((s) => {
        const suspect = suspectByIndex[s.index];
        return (
          <div
            key={s.index}
            className={`spanrow ${activeSpan === s.index ? "active" : ""}`}
            onClick={() => onPickSpan(s.index)}
          >
            <span className="idx">#{s.index}</span> <strong>{s.tool_name}</strong>
            {suspect && <span className={`conf ${suspect.confidence}`}>{suspect.confidence}</span>}
            <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
              {s.status}
              {s.status_message && <span> · {s.status_message}</span>}
              {(s.input_truncated || s.output_truncated) && <span className="trunc"> · body truncated</span>}
            </div>
          </div>
        );
      })}
    </div>
  );
}
