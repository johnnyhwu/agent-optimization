import React from "react";

import Payload from "./SpanPayload.jsx";
import Badge from "./ui/Badge.jsx";
import Card, { CardHeader } from "./ui/Card.jsx";
import { InlineEmpty } from "./ui/EmptyState.jsx";

// Right column: upper = span input/output/token (≈ Langfuse span detail);
// lower = this span's diagnosis reason+evidence, or "not flagged".
//
// The bodies are rendered by `SpanPayload`, which knows the chat-completions
// shape an LLM generation logs. They arrive whole — the view path no longer
// truncates, because cutting a body destroyed the evidence this column exists
// to show. Length is handled by collapsing and scrolling instead.
const CONFIDENCE_TONE = { high: "danger", medium: "warning", low: "neutral" };

export default function SpanDetail({ span, suspect }) {
  if (!span)
    return (
      <Card padded={false} className="col">
        <CardHeader title="Span detail" sticky />
        <InlineEmpty>Pick a step from the trace to inspect what went in and out.</InlineEmpty>
      </Card>
    );
  return (
    <Card padded={false} className="col">
      <CardHeader title={`Step #${span.index} · ${span.tool_name}`} variant="data" sticky />
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

        <div className="label">Diagnosis for this step</div>
        {suspect ? (
          <div>
            <div style={{ marginBottom: 8 }}>
              <Badge tone={CONFIDENCE_TONE[suspect.confidence] || "neutral"}>
                {suspect.confidence} confidence
              </Badge>
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
          <InlineEmpty>This step was not flagged as suspicious.</InlineEmpty>
        )}
      </div>
    </Card>
  );
}
