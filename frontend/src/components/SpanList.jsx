import React from "react";

// Middle column (§6.13): top overall_diagnosis + caveat banner, then the vertical
// span list with suspects marked (confidence high/med/low). Distinguishes
// "generating (retrying)" from "no trace" (§6.12 / §7.1 #5) — and from "the trace
// store rejected us", which used to be shown as "generating" forever.
export default function SpanList({
  trace, activeSpan, onPickSpan, canReDiagnose, onReDiagnose, reDiagnosing, onRetryTrace,
}) {
  if (!trace) return <div className="col"><h4>Trace</h4><div className="notflagged">Select a question.</div></div>;

  const suspectByIndex = {};
  (trace.analysis?.suspects || []).forEach((s) => (suspectByIndex[s.span_index] = s));
  const reDiagnoseButton = canReDiagnose ? (
    <button onClick={onReDiagnose} disabled={reDiagnosing}>
      {reDiagnosing ? "Re-diagnosing…" : "↻ Re-diagnose"}
    </button>
  ) : null;

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

      {/* The whole point of separating this from "generating": a wrong host or a
          rejected key is a thing the developer must go and fix, not wait out. */}
      {trace.trace_state === "error" && (
        <div className="banner error-banner">
          <strong>✕ Could not load the trace.</strong>
          <div className="banner-detail">{trace.trace_error}</div>
          {onRetryTrace && (
            <div style={{ marginTop: 8 }}>
              <button onClick={onRetryTrace}>↻ Retry</button>
            </div>
          )}
        </div>
      )}
      {trace.trace_state === "generating" && (
        <div className="banner generating">
          ⏳ Trace is generating (Langfuse ingestion is async — retrying). This is not
          "no trace"; check back shortly.
          {trace.trace_error && (
            <div className="banner-detail">
              Last attempt during the run failed: {trace.trace_error}
            </div>
          )}
          {onRetryTrace && (
            <div style={{ marginTop: 8 }}>
              <button onClick={onRetryTrace}>↻ Retry</button>
            </div>
          )}
        </div>
      )}
      {trace.trace_state === "no_trace" && (
        <div className="banner generating">No trace — the agent call failed for this question.</div>
      )}

      {trace.analysis && (
        <>
          <div className="banner diagnosis">
            <strong>Diagnosis (clue, not a verdict):</strong> {trace.analysis.overall_diagnosis}
            {reDiagnoseButton && <div style={{ marginTop: 8 }}>{reDiagnoseButton}</div>}
          </div>
          {trace.analysis.caveat && (
            <div className="banner caveat">⚠ Caveat: {trace.analysis.caveat}</div>
          )}
        </>
      )}
      {/* An undiagnosed incorrect question used to look identical whether the
          model errored or was never asked. */}
      {!trace.analysis && trace.diagnosis_error && (
        <div className="banner error-banner">
          <strong>✕ Diagnosis failed.</strong>
          <div className="banner-detail">{trace.diagnosis_error}</div>
          {reDiagnoseButton && <div style={{ marginTop: 8 }}>{reDiagnoseButton}</div>}
        </div>
      )}
      {trace.trace_state === "ready" && !trace.analysis && !trace.diagnosis_error &&
        trace.verdict === "incorrect" && reDiagnoseButton && (
        <div className="banner diagnosis">
          No diagnosis stored for this question yet.
          <div style={{ marginTop: 8 }}>{reDiagnoseButton}</div>
        </div>
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
