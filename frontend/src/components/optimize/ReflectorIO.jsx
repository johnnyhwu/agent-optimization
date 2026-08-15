import React, { useState } from "react";
import Badge from "../ui/Badge.jsx";
import { plural, pluralise } from "../../plural.js";
import Banner from "../ui/Banner.jsx";
import Button from "../ui/Button.jsx";
import { editsProposed, truncationSummary } from "../../optimize_rollout.js";

// One analyst call, opened up: what it was shown, what it answered, and what
// the truncation cascade cut out on the way.
//
// This exists because "why did the optimizer propose that?" is otherwise
// unanswerable. The answer is not the patch — it is the prompt, and in
// particular the parts of the prompt that were not there. A proposal built on
// a trace that lost most of its tool output is a different kind of evidence
// from one built on the whole thing, and the only place that difference can be
// seen is here.
//
// The prompt is shown as it was sent, not rebuilt. A reconstruction looks right
// and differs in exactly the way that mattered.

export default function ReflectorIO({
  minibatch, editReports = [], nApplied, nSkipped, onOpenSkill,
}) {
  const [tab, setTab] = useState("summary");
  const cut = truncationSummary(minibatch);
  const edits = editsProposed(minibatch);
  const summary = minibatch.raw_output?.failure_summary || [];

  return (
    <div className="opt-reflector">
      <div className="opt-reflector-head">
        <strong>Minibatch {minibatch.minibatch_no}</strong>
        <Badge tone={minibatch.source_type === "success" ? "success" : "warning"} size="sm">
          {minibatch.source_type}
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

      <div className="opt-tabs" role="tablist">
        {[
          ["summary", "What it concluded"],
          ["patch", `Proposed patch (${edits})`],
          ["prompt", "Prompt as sent"],
          ["raw", "Raw JSON"],
        ].map(([key, label]) => (
          <Button
            key={key}
            role="tab"
            aria-selected={tab === key}
            variant={tab === key ? "secondary" : "ghost"}
            onClick={() => setTab(key)}
          >
            {label}
          </Button>
        ))}
      </div>

      {/* The analyst returns `failure_summary` as objects — a failure type, a
          count and a description each — and this rendered anything that was not
          already a string with `JSON.stringify`. Since it never was a string,
          the tab a reader opens first showed them a wall of raw JSON:
          {"count":2,"description":"…","failure_type":"rule_missing"}. */}
      {tab === "summary" && <FailureSummary lines={summary} />}

      {tab === "patch" && (
        <PatchList
          patch={minibatch.raw_output?.patch}
          reports={editReports}
          nApplied={nApplied}
          nSkipped={nSkipped}
          onOpenSkill={onOpenSkill}
        />
      )}

      {tab === "prompt" && (
        <>
          <Section title="System">{minibatch.prompt_system}</Section>
          <Section title="User">{minibatch.prompt_user}</Section>
        </>
      )}

      {tab === "raw" && (
        <pre className="opt-pre">
          {minibatch.raw_output
            ? JSON.stringify(minibatch.raw_output, null, 2)
            : "null — the analyst call did not return a patch."}
        </pre>
      )}
    </div>
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
        <Banner tone="warning" title={`${plural(cut.dropped.length, "question")} ${pluralise(cut.dropped.length, "was", "were")} not shown at all`}>
          The batch still did not fit after trimming, so these were dropped
          before the analyst saw them — nothing it proposed can rest on them:{" "}
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
          the two numbers looking like they disagree. */}
      {nApplied != null && (
        <div className="opt-editoutcome">
          <span>
            Across the whole step: <strong>{plural(nApplied, "edit")} applied</strong>
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

function Section({ title, children, compact }) {
  if (!children) return null;
  return (
    <div className={compact ? "opt-io-block compact" : "opt-io-block"}>
      <h5>{title}</h5>
      <pre className="opt-pre">{children}</pre>
    </div>
  );
}
