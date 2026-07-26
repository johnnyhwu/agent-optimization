import React from "react";

// Right column (§6.13): upper = span input/output/token (≈ Langfuse span detail);
// lower = this span's diagnosis reason+evidence, or "not flagged".
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
      <div className="kv">
        <div className="tokens">
          <div className="t">in: <strong>{span.token_usage.input ?? "—"}</strong></div>
          <div className="t">out: <strong>{span.token_usage.output ?? "—"}</strong></div>
          <div className="t">total: <strong>{span.token_usage.total ?? "—"}</strong></div>
        </div>

        <div className="label">
          Input {span.input_truncated && <span className="trunc">(truncated)</span>}
        </div>
        <pre>{span.input}</pre>

        <div className="label">
          Output {span.output_truncated && <span className="trunc">(truncated — §6.7 body cut, span kept)</span>}
        </div>
        <pre>{span.output}</pre>

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
