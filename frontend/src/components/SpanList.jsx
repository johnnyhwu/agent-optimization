import React, { useLayoutEffect, useRef, useState } from "react";
import Badge from "./ui/Badge.jsx";
import Banner, { BannerDetail } from "./ui/Banner.jsx";
import Button from "./ui/Button.jsx";
import Card, { CardHeader } from "./ui/Card.jsx";
import { InlineEmpty } from "./ui/EmptyState.jsx";
import { IconClock, IconRefresh } from "./icons.jsx";
import { showRawName, spanLabel } from "../span_label.js";
import { isTimeout, timeoutAdvice, timeoutTitle } from "../failure.js";

// A one-word class for the step, so the column can be scanned for shape before
// it is read for detail. Deliberately short: the interesting text is the tool
// name beside it.
const STEP_KIND_LABEL = {
  tool_call: "calls",
  tool_result: "result",
  assistant: "answers",
  raw: "step",
};
const STEP_KIND_HINT = {
  tool_call: "This step asked for one or more tools",
  tool_result: "A tool's result came back into this step",
  assistant: "The model answered in words rather than calling a tool",
  raw: "The trace does not say what this step did — showing the name it was logged under",
};

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
  if (!known) return <BannerDetail>{raw}</BannerDetail>;
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
// The question this column is about.
//
// It was nowhere on this screen. Both left columns elide it — one row, one line,
// ellipsis — so a question longer than the column was unreadable in the entire
// interface, which is a problem when the thing being judged is whether an answer
// matches it.
//
// Clamped rather than dumped: the trace below is what most visits are for, and a
// six-line question would push it under the fold on every attempt to buy full
// text on the few that need it.
//
// Whether the toggle appears is *measured*, not guessed from the string length.
// A character count cannot know the column's width, and this column is resized
// by collapsing the attempt list as well as by the window — so a guess produces
// the two worst outcomes: an offer to reveal text that is already fully visible,
// and no offer on the questions that are actually cut off.
function QuestionSection({ question }) {
  const ref = useRef(null);
  const [open, setOpen] = useState(false);
  const [clipped, setClipped] = useState(false);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    // Only meaningful while clamped; expanded, the answer is already "yes".
    const measure = () => {
      if (!open) setClipped(el.scrollHeight > el.clientHeight + 1);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [question, open]);

  return (
    <Section title="Question">
      <div ref={ref} className={`question-text${open ? "" : " is-clamped"}`}>
        {question}
      </div>
      {clipped && (
        <Button variant="link" size="sm" onClick={() => setOpen((v) => !v)}>
          {open ? "Show less" : "Show the whole question"}
        </Button>
      )}
    </Section>
  );
}

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
  onRetryTrace, playground = false, emptyHint, question,
}) {
  if (!trace) {
    return (
      <Card padded={false} className="col">
        <CardHeader title="Trace & diagnosis" sticky />
        <InlineEmpty>
          {refreshing ? "Loading…" : emptyHint || "Pick a question to see its trace."}
        </InlineEmpty>
      </Card>
    );
  }

  const suspectByIndex = {};
  (trace.analysis?.suspects || []).forEach((s) => (suspectByIndex[s.span_index] = s));
  const reDiagnoseButton = canReDiagnose ? (
    <Button size="sm" icon={<IconRefresh size={13} />} onClick={onReDiagnose} loading={reDiagnosing}>
      {reDiagnosing ? "Re-diagnosing…" : "Re-diagnose"}
    </Button>
  ) : null;

  // One of these always renders, so the section is never an empty labelled band.
  let diagnosis;
  if (trace.analysis) {
    diagnosis = (
      <>
        <Banner tone="info" title="A clue, not a verdict" actions={reDiagnoseButton}>
          {trace.analysis.overall_diagnosis}
        </Banner>
        {trace.analysis.caveat && (
          <Banner tone="warning" title="Caveat">{trace.analysis.caveat}</Banner>
        )}
      </>
    );
  } else if (trace.diagnosis_error) {
    // An undiagnosed incorrect question used to look identical whether the
    // model errored or was never asked.
    diagnosis = (
      <Banner tone="error" title="Diagnosis failed." actions={reDiagnoseButton}>
        <BannerDetail>{trace.diagnosis_error}</BannerDetail>
      </Banner>
    );
  } else if (trace.verdict === "correct") {
    diagnosis = <Banner tone="info">Correct answer — nothing to diagnose.</Banner>;
  } else if (playground) {
    // In the playground the switch is the expected reasoning process, not the
    // verdict: with one supplied there is a diagnosis, without one there is
    // nothing to compare the trace against.
    diagnosis = reDiagnoseButton ? (
      <Banner tone="info" actions={reDiagnoseButton}>No diagnosis for this attempt yet.</Banner>
    ) : (
      <Banner tone="info">
        No diagnosis — add an expected reasoning process and the trace will be
        compared against it.
      </Banner>
    );
  } else if (trace.verdict === "incorrect" && reDiagnoseButton) {
    diagnosis = (
      <Banner tone="info" actions={reDiagnoseButton}>
        No diagnosis stored for this question yet.
      </Banner>
    );
  } else {
    diagnosis = (
      <Banner tone="info">
        No diagnosis — a question is diagnosed once it has been judged incorrect.
      </Banner>
    );
  }

  return (
    <Card padded={false} className="col">
      <CardHeader
        title="Trace & diagnosis"
        sticky
        /* The panel keeps its content while a live question refetches, so this
           dot is the only thing that says an update is on the way. */
        actions={refreshing ? <span className="refreshing" title="Updating…" /> : null}
      />

      {/* Above the sections, because it explains all of them at once. */}
      {/* In the playground the message is already a whole sentence — and one of
          them describes a deliberate stop, which "hit a problem" would
          misrepresent — so it stands on its own. */}
      {/* A timeout is not a crash, and showing it as one sent developers looking
          for a broken agent when what they had was a limit. The clock says which
          of the two it is before the sentence is read, and the note says where
          the limit that stopped this actually lives. */}
      {trace.error_message && (
        isTimeout(trace.failure_kind) ? (
          <Banner
            tone="error"
            icon={<IconClock size={15} />}
            title={timeoutTitle(trace.failure_kind)}
          >
            {trace.error_message}
            <div className="ui-banner-note">
              {timeoutAdvice(trace.failure_kind, { playground })}
            </div>
          </Banner>
        ) : (
          <Banner tone="error" title={playground ? null : "This question failed."}>
            {trace.error_message}
          </Banner>
        )
      )}

      {/* Above the answer, because an answer is only readable against the
          question it answers. */}
      {question && <QuestionSection question={question} />}

      {/* What the agent answered, next to what it was graded against. With a real
          agent this is the first thing to read — the verdict alone doesn't say
          what went wrong. */}
      <Section title="Answer">
        <div className="answers">
          <div className="label">
            Agent answer
            {trace.verdict && (
              <Badge tone={trace.verdict === "correct" ? "success" : "danger"}>{trace.verdict}</Badge>
            )}
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
          <Banner
            tone="error"
            title="Could not load the trace."
            actions={onRetryTrace && <RetryButton onClick={onRetryTrace} />}
          >
            <TraceErrorBody raw={trace.trace_error} />
          </Banner>
        )}
        {trace.trace_state === "generating" && (
          <Banner
            tone="pending"
            title="The trace hasn't arrived yet."
            actions={onRetryTrace && <RetryButton onClick={onRetryTrace} />}
          >
            Traces are recorded asynchronously, so there is a short delay after the
            agent answers. This is not a missing trace — check back shortly.
            {trace.trace_error && (
              <>
                <div className="ui-banner-note">The last attempt during the run failed:</div>
                <TraceErrorBody raw={trace.trace_error} />
              </>
            )}
          </Banner>
        )}
        {/* Distinct from "generating": nothing is being waited on because nothing
            has been asked yet. Showing an ingestion message here — or, worse, a
            trace-store error from a fetch that should never have happened — made a
            brand-new run look like it had already failed. */}
        {trace.trace_state === "not_started" && (
          <Banner tone="pending" title="Waiting for the agent.">
            This question hasn't been sent yet. Its trace appears once the agent answers.
          </Banner>
        )}
        {trace.trace_state === "no_trace" && (
          <Banner tone="warning" title="No trace.">
            The agent call failed for this question, so nothing was recorded.
          </Banner>
        )}

        {trace.spans.map((s) => {
          const suspect = suspectByIndex[s.index];
          // What the step did, read out of its own payload. The trace store's
          // own name for it is usually the same string on every row (see
          // span_label.js), so it is shown only when it says something.
          const derived = spanLabel(s);
          return (
            <div
              key={s.index}
              className={`spanrow ${activeSpan === s.index ? "active" : ""}`}
              role="button"
              tabIndex={0}
              onClick={() => onPickSpan(s.index)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onPickSpan(s.index);
                }
              }}
            >
              <div className="spanrow-head">
                <span className="idx">#{s.index}</span>
                <span className={`spankind ${derived.kind}`} title={STEP_KIND_HINT[derived.kind]}>
                  {STEP_KIND_LABEL[derived.kind]}
                </span>
                <strong title={derived.detail || undefined}>{derived.label}</strong>
                {suspect && (
                  <Badge
                    tone={suspect.confidence === "high" ? "danger" : suspect.confidence === "medium" ? "warning" : "neutral"}
                    size="sm"
                    className="spanrow-conf"
                  >
                    {suspect.confidence} confidence
                  </Badge>
                )}
              </div>
              <div className="spanrow-status">
                {s.status}
                {s.status_message && <span> · {s.status_message}</span>}
                {showRawName(s, derived) && <span> · {s.tool_name}</span>}
              </div>
            </div>
          );
        })}
      </Section>
    </Card>
  );
}

// The trace is fetched live, so "it isn't there" and "it isn't there yet" both
// end in the same offer.
function RetryButton({ onClick }) {
  return (
    <Button size="sm" icon={<IconRefresh size={13} />} onClick={onClick}>
      Retry
    </Button>
  );
}
