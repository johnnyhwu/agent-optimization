import React from "react";

import Payload from "./SpanPayload.jsx";
import Badge from "./ui/Badge.jsx";
import Card, { CardHeader } from "./ui/Card.jsx";
import { InlineEmpty } from "./ui/EmptyState.jsx";
import { secs } from "../duration.js";
import { showRawName, spanLabel } from "../span_label.js";

// Right column: upper = span input/output/token (≈ Langfuse span detail);
// lower = this span's diagnosis reason+evidence, or "not flagged".
//
// The bodies are rendered by `SpanPayload`, which knows the chat-completions
// shape an LLM generation logs. They arrive whole — the view path no longer
// truncates, because cutting a body destroyed the evidence this column exists
// to show. Length is handled by collapsing and scrolling instead.
const CONFIDENCE_TONE = { high: "danger", medium: "warning", low: "neutral" };

export default function SpanDetail({ span, suspect }) {
  const derived = spanLabel(span);
  if (!span)
    return (
      <Card padded={false} className="col">
        <CardHeader title="Span detail" sticky />
        <InlineEmpty>Pick a step from the trace to inspect what went in and out.</InlineEmpty>
      </Card>
    );
  return (
    <Card padded={false} className="col">
      <CardHeader title={`Step #${span.index} · ${derived.label}`} variant="data" sticky />
      {/* Keyed by span: disclosures the developer opened belong to the span they
          opened them on, not to whichever span next occupies this column. */}
      <div className="kv" key={span.index}>
        {/* The name the trace store actually holds, where it differs from what
            the payload says the step did — a step has to stay findable in
            Langfuse's own UI. */}
        {showRawName(span, derived) && (
          <div className="span-rawname">logged as <code>{span.tool_name}</code></div>
        )}
        {/* What this one step cost, in both currencies.
            Tokens were here from the start; time was not, and it is the half a
            developer reaches for first — the question already says it took nine
            seconds, and only this says whether that was one slow model call or a
            tool that hung. Last in the row because it is the newest and because
            the three token figures are a set. */}
        <div className="tokens">
          <div className="t">in: <strong>{span.token_usage.input ?? "—"}</strong></div>
          <div className="t">out: <strong>{span.token_usage.output ?? "—"}</strong></div>
          <div className="t">total: <strong>{span.token_usage.total ?? "—"}</strong></div>
          <div className="t" title="How long this step took, from the trace store's own timestamps">
            took: <strong>{secs(span.latency_ms)}</strong>
          </div>
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
