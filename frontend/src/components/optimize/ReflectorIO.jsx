import React from "react";
import Badge from "../ui/Badge.jsx";
import { plural, pluralise } from "../../plural.js";
import Banner from "../ui/Banner.jsx";
import Button from "../ui/Button.jsx";
import { Collapsible } from "../SpanPayload.jsx";
import {
  SOURCE_LABEL,
  SOURCE_TONE,
  editsProposed,
  minibatchLabel,
  truncationSummary,
} from "../../optimize_rollout.js";

// One analyst call, opened up: what it concluded, what it asked for, and — for
// anyone who needs it — exactly what it was shown.
//
// This exists because "why did the optimizer propose that?" is otherwise
// unanswerable. Part of the answer is the patch and part of it is the prompt,
// and in particular the parts of the prompt that were not there: a proposal
// built on a trace that lost most of its tool output is a different kind of
// evidence from one built on the whole thing.
//
// But those two parts are not read equally often. The conclusion and the patch
// are what every visit is for; the prompt is what a small number of visits are
// for, and it is thousands of lines long. They used to be four tabs of equal
// weight — with a Raw JSON tab nobody asked for — so the page made a reader
// choose between two halves of one answer, and offered the wall of text at the
// same size as the sentence. Now the answer is one block and the evidence is one
// click below it.
//
// The prompt is shown as it was sent, not rebuilt. A reconstruction looks right
// and differs in exactly the way that mattered.

export default function ReflectorIO({
  minibatch, editReports = [], nApplied, nSkipped, onOpenSkill, mode = "isolated",
}) {
  const cut = truncationSummary(minibatch);
  const edits = editsProposed(minibatch);
  const summary = minibatch.raw_output?.failure_summary || [];

  return (
    <div className="opt-reflector">
      <div className="opt-reflector-head">
        <strong>{minibatchLabel(minibatch, { mode })}</strong>
        <Badge tone={SOURCE_TONE[minibatch.source_type] || "neutral"} size="sm">
          {SOURCE_LABEL[minibatch.source_type] || minibatch.source_type}
        </Badge>
        <span className="muted">{minibatch.n_items} questions</span>
        <span className="muted">{plural(edits, "edit")} proposed</span>
        {minibatch.duration_ms != null && (
          <span className="muted">{(minibatch.duration_ms / 1000).toFixed(1)}s</span>
        )}
      </div>

      {minibatch.error && (
        <Banner tone="error" title="This analyst call failed">
          {minibatch.error}
          <br />
          The step carried on with the patches it did get, so this batch's
          failures contributed nothing to the edits below.
        </Banner>
      )}

      <TruncationNote cut={cut} />

      <h4 className="opt-block-title">What this analyst concluded</h4>
      {/* The analyst returns `failure_summary` as objects — a failure type, a
          count and a description each — and this once rendered anything that was
          not already a string with `JSON.stringify`. Since it never was a
          string, the first thing a reader saw was a wall of raw JSON. */}
      <FailureSummary lines={summary} />

      {/* No count in this heading: the line at the top of the card already
          says how many edits were proposed, and two numbers for one quantity
          invite a reader to look for the difference between them. */}
      <h4 className="opt-block-title">What it asked for</h4>
      <PatchList
        patch={minibatch.raw_output?.patch}
        reports={editReports}
        nApplied={nApplied}
        nSkipped={nSkipped}
        onOpenSkill={onOpenSkill}
      />

      <PromptRecord
        system={minibatch.prompt_system}
        user={minibatch.prompt_user}
        output={minibatch.raw_output}
      />
    </div>
  );
}

// The evidence, one click away: what this call was sent and what came back.
//
// Closed by default and deliberately last. The reply is shown as the parsed
// object rather than the model's raw text — every one of these calls is a JSON
// contract, and the parse is what the rest of the page is built from, so
// showing anything else here would be showing a different answer from the one
// that was acted on.
export function PromptRecord({ system, user, output, label = "the analyst" }) {
  if (!system && !user && !output) return null;
  return (
    <Collapsible
      className="opt-promptlog"
      title={<span className="opt-block-title">What {label} was sent, and what came back</span>}
      meta={user
        ? `${user.length.toLocaleString()} ${pluralise(user.length, "char")}`
        : null}
    >
      <Section title="System prompt">{system}</Section>
      <Section title="User prompt">{user}</Section>
      <Section title="Reply">
        {output
          ? JSON.stringify(output, null, 2)
          : "Nothing usable came back from this call."}
      </Section>
    </Collapsible>
  );
}

// The truncation ledger, said out loud. The developer asked for this to be
// visible rather than inferable: a batch that lost 70% of its evidence produces
// a confident-looking patch that nothing on the page would otherwise question.
function TruncationNote({ cut }) {
  if (!cut.truncated) {
    return (
      <p className="opt-hint">
        {cut.nItems} questions · nothing was truncated
        {cut.before != null && ` (${cut.before.toLocaleString()} chars, within budget)`}
      </p>
    );
  }
  return (
    <div className="opt-truncation">
      <span>
        {cut.nItems} questions · <strong>{plural(cut.itemsTruncated, "trace")} truncated</strong>
        {cut.before != null && cut.after != null && (
          <> · {cut.before.toLocaleString()} → {cut.after.toLocaleString()} chars</>
        )}
      </span>
      {cut.dropped.length > 0 && (
        <Banner tone="warning" title={`${plural(cut.dropped.length, "run")} ${pluralise(cut.dropped.length, "was", "were")} withheld`}>
          The batch still did not fit after trimming, so these questions reached
          the analyst without their trajectories — it saw the question, both
          answers and the verdict, and was told the run itself was withheld.
          Nothing it concluded about <em>how</em> they failed can rest on them:{" "}
          <code>{cut.dropped.join(", ")}</code>
        </Banner>
      )}
    </div>
  );
}

// What each proposed failure actually was. Three fields, laid out as three
// fields.
function FailureSummary({ lines }) {
  if (!lines.length) return <p className="opt-hint">This analyst returned no failure summary.</p>;
  return (
    <ul className="opt-reflector-summary">
      {lines.map((line, i) => {
        if (typeof line === "string") return <li key={i}>{line}</li>;
        return (
          <li key={i}>
            <span className="opt-failure-head">
              <Badge size="sm" mono>{(line.failure_type || "failure").replace(/_/g, " ")}</Badge>
              {line.count != null && (
                <span className="muted">{plural(line.count, "question")}</span>
              )}
            </span>
            <span className="opt-failure-desc">{line.description || "—"}</span>
          </li>
        );
      })}
    </ul>
  );
}

// The patch, and what became of it.
//
// The proposal on its own raises exactly one question — did any of this reach
// the skill? — and answering it used to mean leaving the page. The step's edit
// reports say per edit, so each proposal now carries its own outcome, and the
// summary line above them is the step's own count.
function PatchList({ patch, reports = [], nApplied, nSkipped, onOpenSkill }) {
  const edits = patch?.edits || [];
  if (!edits.length) return <p className="opt-hint">No edits were proposed.</p>;
  return (
    <div className="opt-editlist">
      {patch.reasoning && (
        <div className="opt-rationale">
          <span className="opt-rationale-label">Analyst's rationale</span>
          <p>{patch.reasoning}</p>
        </div>
      )}

      {/* The step's outcome, not this minibatch's: the analysts' patches are
          merged and ranked before anything is applied, so an edit proposed here
          may be collapsed into another one's. Saying whose count it is stops
          the two numbers looking like they disagree.

          In routing the two populations are the same one — a single call, no
          merge — so the framing is dropped rather than sending a reader to
          reconcile a difference that cannot exist. */}
      {nApplied != null && (
        <div className="opt-editoutcome">
          <span>
            {mode === "routing" ? "" : "Across the whole step: "}
            <strong>{plural(nApplied, "edit")} applied</strong>
            {nSkipped ? `, ${nSkipped} refused` : ""}
          </span>
          {onOpenSkill && (
            <Button variant="secondary" size="sm" onClick={onOpenSkill}>
              View skill diff
            </Button>
          )}
        </div>
      )}

      {edits.map((edit, i) => {
        // Matched on path and op — the reports are the step's, after merging, so
        // this is a best-effort tie-back rather than an index lookup. When no
        // report matches, nothing is claimed.
        const report = reports.find(
          (r) => r.path === edit.path && (!r.op || r.op === edit.op),
        );
        return (
          <div key={i} className="opt-edit">
            <div className="opt-edit-head">
              <Badge size="sm" mono>{edit.op || "edit"}</Badge>
              <code>{edit.path || "(no path)"}</code>
              {report && (
                <Badge
                  tone={report.status?.startsWith("applied") ? "success" : "warning"}
                  size="sm"
                >
                  {report.status?.startsWith("applied") ? "applied" : "refused"}
                </Badge>
              )}
            </div>
            {edit.target && (
              <Section title="Anchor" compact>{edit.target}</Section>
            )}
            <Section title="Content" compact>{edit.content}</Section>
          </div>
        );
      })}
    </div>
  );
}

export function Section({ title, children, compact }) {
  if (!children) return null;
  return (
    <div className={compact ? "opt-io-block compact" : "opt-io-block"}>
      <h5>{title}</h5>
      <pre className="opt-pre">{children}</pre>
    </div>
  );
}
