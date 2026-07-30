import React from "react";

// Some trace-store failures are worth explaining rather than quoting. A raw
// ClickHouse dump tells a developer nothing about what to do next, and — because
// it comes back from *our* API — reads as if this platform were at fault.
//
// The `events` / `events_core` table is Langfuse's own v4 schema, which some
// self-hosted builds (~3.152+) query without having shipped the migration that
// creates it. Nothing here generates SQL; Langfuse does.
const KNOWN_TRACE_ERRORS = [
  {
    match: /unknown table expression|events_core/i,
    title: "Your Langfuse server could not query its own storage.",
    body:
      "This is a known self-hosted Langfuse issue: builds from ~3.152 query a " +
      "ClickHouse `events` table whose migration hasn't shipped. It is a " +
      "Langfuse deployment problem, not an eval-platform one — re-run the " +
      "ClickHouse migrations (check `schema_migrations` for `dirty = 1`), or " +
      "pin the Langfuse image below 3.152.",
  },
  {
    match: /HTTP 401|HTTP 403|invalid credentials/i,
    title: "Langfuse rejected the credentials for this run.",
    body:
      "The public/secret key pair this run was triggered with is wrong or lacks " +
      "access to the project. Re-enter it in the run config and trigger again.",
  },
  {
    match: /ConnectError|ConnectTimeout|Could not reach/i,
    title: "Langfuse was unreachable.",
    body: "The host is wrong, down, or not routable from the backend container.",
  },
];

function explainTraceError(raw) {
  if (!raw) return null;
  return KNOWN_TRACE_ERRORS.find((e) => e.match.test(raw)) || null;
}

// Renders the explanation when we have one, with the raw text kept behind a
// disclosure — it's still the thing to paste into a bug report.
function TraceErrorBody({ raw }) {
  const known = explainTraceError(raw);
  if (!known) return <div className="banner-detail">{raw}</div>;
  return (
    <>
      {/* Prose, not the monospace treatment `.banner-detail` gives raw output. */}
      <div className="banner-explain">
        <strong>{known.title}</strong> {known.body}
      </div>
      <details className="banner-raw">
        <summary>Technical detail</summary>
        <pre>{raw}</pre>
      </details>
    </>
  );
}

// This column carries four unrelated kinds of content — what the agent said,
// what it should have said, why it was judged that way, and the trace itself.
// Run together they read as one wall of text, so each gets a labelled band.
function Section({ title, count, children }) {
  return (
    <section className="section">
      <div className="section-head">
        <span className="section-title">{title}</span>
        {count != null && <span className="section-count">{count}</span>}
      </div>
      {children}
    </section>
  );
}

// Middle column (§6.13): answer, then overall_diagnosis + caveat banner, then the
// vertical span list with suspects marked (confidence high/med/low). Distinguishes
// "generating (retrying)" from "no trace" (§6.12 / §7.1 #5) — and from "the trace
// store rejected us", which used to be shown as "generating" forever.
//
// `playground` switches the two places that assume a graded eval question
// (§10.5): an attempt may have no expected answer at all, so promising an
// "Expected answer" row and explaining that diagnosis follows an incorrect
// verdict would both be false there.
export default function SpanList({
  trace, refreshing, activeSpan, onPickSpan, canReDiagnose, onReDiagnose, reDiagnosing,
  onRetryTrace, playground = false, emptyHint,
}) {
  if (!trace) {
    return (
      <div className="col">
        <h4>Trace &amp; diagnosis</h4>
        <div className="notflagged">
          {refreshing ? "Loading…" : emptyHint || "Select a question."}
        </div>
      </div>
    );
  }

  const suspectByIndex = {};
  (trace.analysis?.suspects || []).forEach((s) => (suspectByIndex[s.span_index] = s));
  const reDiagnoseButton = canReDiagnose ? (
    <button onClick={onReDiagnose} disabled={reDiagnosing}>
      {reDiagnosing ? "Re-diagnosing…" : "↻ Re-diagnose"}
    </button>
  ) : null;

  // One of these always renders, so the section is never an empty labelled band.
  let diagnosis;
  if (trace.analysis) {
    diagnosis = (
      <>
        <div className="banner diagnosis">
          <strong>Diagnosis (clue, not a verdict):</strong> {trace.analysis.overall_diagnosis}
          {reDiagnoseButton && <div style={{ marginTop: 8 }}>{reDiagnoseButton}</div>}
        </div>
        {trace.analysis.caveat && (
          <div className="banner caveat">⚠ Caveat: {trace.analysis.caveat}</div>
        )}
      </>
    );
  } else if (trace.diagnosis_error) {
    // An undiagnosed incorrect question used to look identical whether the
    // model errored or was never asked.
    diagnosis = (
      <div className="banner error-banner">
        <strong>✕ Diagnosis failed.</strong>
        <div className="banner-detail">{trace.diagnosis_error}</div>
        {reDiagnoseButton && <div style={{ marginTop: 8 }}>{reDiagnoseButton}</div>}
      </div>
    );
  } else if (trace.verdict === "correct") {
    diagnosis = (
      <div className="banner diagnosis muted">Correct answer — no diagnosis generated.</div>
    );
  } else if (playground) {
    // In the playground the switch is the expected reasoning process, not the
    // verdict: with one supplied there is a diagnosis, without one there is
    // nothing to compare the trace against.
    diagnosis = reDiagnoseButton ? (
      <div className="banner diagnosis">
        No diagnosis for this attempt yet.
        <div style={{ marginTop: 8 }}>{reDiagnoseButton}</div>
      </div>
    ) : (
      <div className="banner diagnosis muted">
        No diagnosis — add an expected reasoning process to have the trace
        compared against it.
      </div>
    );
  } else if (trace.verdict === "incorrect" && reDiagnoseButton) {
    diagnosis = (
      <div className="banner diagnosis">
        No diagnosis stored for this question yet.
        <div style={{ marginTop: 8 }}>{reDiagnoseButton}</div>
      </div>
    );
  } else {
    diagnosis = (
      <div className="banner diagnosis muted">
        No diagnosis — a question is diagnosed once it has been judged incorrect.
      </div>
    );
  }

  return (
    <div className="col">
      <h4>
        Trace &amp; diagnosis
        {/* The panel keeps its content while a live question refetches, so this
            dot is the only thing that says an update is on the way. */}
        {refreshing && <span className="refreshing" title="Updating…" />}
      </h4>

      {/* Above the sections, because it explains all of them at once. */}
      {/* In the playground the message is already a whole sentence — and one of
          them describes a deliberate stop, which "hit a problem" would
          misrepresent — so it stands on its own. */}
      {trace.error_message && (
        <div className="banner error-banner">
          {playground
            ? trace.error_message
            : `✕ This question failed: ${trace.error_message}`}
        </div>
      )}

      {/* What the agent answered, next to what it was graded against. With a real
          agent this is the first thing to read — the verdict alone doesn't say
          what went wrong. */}
      <Section title="Answer">
        <div className="answers">
          <div className="label">
            Agent answer
            {trace.verdict && <span className={`verdict ${trace.verdict}`}>{trace.verdict}</span>}
          </div>
          <pre>{trace.agent_response || "— (no answer recorded)"}</pre>
          {/* An attempt with no expected answer was never graded, so a row
              promising one with a dash in it would misrepresent the attempt
              rather than describe it. */}
          {(!playground || trace.ground_truth_response) && (
            <>
              <div className="label">Expected answer</div>
              <pre>{trace.ground_truth_response || "—"}</pre>
            </>
          )}
          {playground && !trace.ground_truth_response && (
            <div className="hint">
              No expected answer given — this attempt was not judged.
            </div>
          )}
          {trace.judge_comment && (
            <>
              <div className="label">Judge</div>
              <div className="judge-comment">{trace.judge_comment}</div>
            </>
          )}
        </div>
      </Section>

      <Section title="Diagnosis">{diagnosis}</Section>

      <Section title="Trace" count={trace.trace_state === "ready" ? `${trace.spans.length} spans` : null}>
        {/* The trace-state banners live here rather than at the top of the
            column: they are all statements about the span list below, not about
            the answer or the diagnosis. */}

        {/* The whole point of separating this from "generating": a wrong host or a
            rejected key is a thing the developer must go and fix, not wait out. */}
        {trace.trace_state === "error" && (
          <div className="banner error-banner">
            <strong>✕ Could not load the trace.</strong>
            <TraceErrorBody raw={trace.trace_error} />
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
              <>
                <div className="banner-detail">Last attempt during the run failed:</div>
                <TraceErrorBody raw={trace.trace_error} />
              </>
            )}
            {onRetryTrace && (
              <div style={{ marginTop: 8 }}>
                <button onClick={onRetryTrace}>↻ Retry</button>
              </div>
            )}
          </div>
        )}
        {/* Distinct from "generating": nothing is being waited on because nothing
            has been asked yet. Showing an ingestion message here — or, worse, a
            trace-store error from a fetch that should never have happened — made a
            brand-new run look like it had already failed. */}
        {trace.trace_state === "not_started" && (
          <div className="banner generating">
            ⏳ Waiting for the agent — this question hasn't been sent yet. The trace
            appears once it answers.
          </div>
        )}
        {trace.trace_state === "no_trace" && (
          <div className="banner generating">No trace — the agent call failed for this question.</div>
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
              </div>
            </div>
          );
        })}
      </Section>
    </div>
  );
}
