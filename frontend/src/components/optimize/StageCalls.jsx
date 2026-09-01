import React from "react";
import Badge from "../ui/Badge.jsx";
import Banner from "../ui/Banner.jsx";
import { Collapsible } from "../SpanPayload.jsx";
import { PromptRecord } from "./ReflectorIO.jsx";
import { plural } from "../../plural.js";
import { callLabel, editCount, groupStageCalls, summarise } from "../../optimize_stage_calls.js";

// What happened between the analysts and the skill.
//
// Every other part of this page is per-minibatch, and these calls are not: one
// merge and one ranking serve the whole step. That is why they sit in the header
// card rather than in the list — a "Minibatch 4" that was actually the merge
// would misrepresent what an analyst saw, which is the one claim this page is
// making.
//
// It is closed by default for the same reason the analyst's prompt is: most
// visits are about what an analyst concluded, and this answers the rarer and
// more specific question — where did my edit go? An edit proposed by an analyst
// and absent from the skill was dropped in exactly one of these calls.
//
// **A step with no stages has two different reasons, and they used to read as
// one.** The old empty state said the step "ran before this page kept them",
// which is true of a run from before the recording existed and false of every
// routing step: those make one analyst call, and a single patch is returned
// untouched by both stages without the model being called, so there is nothing
// to record. Sending a reader to look for a migration problem that is not there
// is worse than saying nothing.
//
// The applied/refused line moved out of the collapsible for the same reason it
// was worth noticing: it lived in the non-empty branch, so exactly the steps
// with no stages — every routing step — lost the one sentence saying what the
// step actually did to the skill.

export default function StageCalls({
  stageCalls = [], nApplied, nSkipped, editSummary, mode = "isolated",
}) {
  const groups = groupStageCalls(stageCalls);
  const outcome = nApplied != null && (
    <div className="opt-editoutcome">
      <span>
        The step applied <strong>{plural(nApplied, "edit")}</strong>
        {nSkipped ? `, and ${nSkipped} were refused` : ""}
        {editSummary ? ` — “${editSummary}”` : ""}
      </span>
    </div>
  );

  if (!groups.length) {
    return (
      <>
        <p className="opt-hint">
          {mode === "routing"
            ? "There was no merging or ranking: a routing step makes one analyst " +
              "call over its whole batch, and a single patch passes through both " +
              "stages untouched. What it proposed is below, and what became of it " +
              "is in the skill diff."
            : "Merging and ranking were not recorded for this step — it ran before " +
              "this page kept them. What each analyst proposed is still below; what " +
              "became of it is in the skill diff."}
        </p>
        {outcome}
      </>
    );
  }

  const total = stageCalls.length;
  return (
    <Collapsible
      className="opt-stagecalls"
      title={<span className="opt-block-title">After the analysts: merging and ranking</span>}
      meta={`${plural(total, "call")}`}
    >
      <p className="opt-hint">
        The analysts propose independently, so their patches are merged and — if
        the pool is over the step's learning rate — ranked before anything is
        applied. An edit asked for below and missing from the skill was dropped
        here.
      </p>

      {groups.map((group) => (
        <section key={group.key} className="opt-stage">
          <h4 className="opt-block-title">{group.title}</h4>
          <p className="opt-hint">{group.blurb}</p>
          {group.calls.map((call, i) => (
            <StageCall
              key={call.seq}
              call={call}
              label={callLabel(call, i, group.calls.length)}
            />
          ))}
        </section>
      ))}

      {outcome}
    </Collapsible>
  );
}

function StageCall({ call, label }) {
  const n = editCount(call.output);
  return (
    <div className={call.error ? "opt-stage-call has-error" : "opt-stage-call"}>
      <div className="opt-stage-head">
        {label && <Badge size="sm" mono>{label}</Badge>}
        {n != null && <span className="muted">{plural(n, "edit")} out</span>}
        {call.duration_ms != null && (
          <span className="muted">{(call.duration_ms / 1000).toFixed(1)}s</span>
        )}
      </div>

      {/* A stage that could not be parsed did not merely fail to display: its
          patch was discarded and the inputs were concatenated instead. That
          changes what the step applied, so it is stated rather than left as an
          empty panel. */}
      {call.error ? (
        <Banner tone="warning" title="This call did not produce a usable answer">
          {call.error}
        </Banner>
      ) : (
        <p className="opt-stage-summary">{summarise(call)}</p>
      )}

      <PromptRecord
        system={call.prompt_system}
        user={call.prompt_user}
        output={call.output}
        label="this stage"
      />
    </div>
  );
}
