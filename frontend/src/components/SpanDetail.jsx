import React from "react";

import Payload from "./SpanPayload.jsx";

// Right column (§6.13): upper = span input/output/token (≈ Langfuse span detail);
// lower = this span's diagnosis reason+evidence, or "not flagged".
//
// The bodies are rendered by `SpanPayload`, which knows the chat-completions
// shape an LLM generation logs. They arrive whole — the view path no longer
// truncates, because cutting a body destroyed the evidence this column exists
// to show. Length is handled by collapsing and scrolling instead.
export default function SpanDetail({ span, suspect }) {
  if (!span)
    return (
      <div className="col">
        <h4>Span detail</h4>
        <div className="notflagged">Select a span to inspect.</div>
      </div>
    );
  return (
    <div className="col">
      <h4>
        Span #{span.index} · {span.tool_name}
      </h4>
      {/* Keyed by span: disclosures the developer opened belong to the span they
          opened them on, not to whichever span next occupies this column. */}
      <div className="kv" key={span.index}>
        <div className="tokens">
          <div className="t">in: <strong>{span.token_usage.input ?? "—"}</strong></div>
          <div className="t">out: <strong>{span.token_usage.output ?? "—"}</strong></div>
          <div className="t">total: <strong>{span.token_usage.total ?? "—"}</strong></div>
        </div>

        <Payload label="Input" value={span.input} />
        <Payload label="Output" value={span.output} />

        <div className="label">Diagnosis for this span</div>
        {suspect ? (
          <div>
            <div style={{ marginBottom: 6 }}>
              <span className={`conf ${suspect.confidence}`}>{suspect.confidence}</span>
            </div>
            <div style={{ marginBottom: 8 }}>
              <strong>Why suspicious:</strong> {suspect.reason}
            </div>
            <div>
              <strong>Evidence:</strong>
              <pre>{suspect.evidence}</pre>
            </div>
          </div>
        ) : (
          <div className="notflagged">This step was not flagged as suspicious.</div>
        )}
      </div>
    </div>
  );
}
