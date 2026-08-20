import React, { useEffect, useState } from "react";
import { api } from "../../api.js";
import Badge from "../ui/Badge.jsx";
import Banner from "../ui/Banner.jsx";
import Button from "../ui/Button.jsx";
import Card, { CardHeader } from "../ui/Card.jsx";
import Skeleton from "../ui/Skeleton.jsx";
import SpanList from "../SpanList.jsx";
import SpanDetail from "../SpanDetail.jsx";
import {
  IconAlert,
  IconArrowLeft,
  IconCheck,
  IconDot,
  IconHalfCircle,
  IconX,
} from "../icons.jsx";
import { secs } from "../../duration.js";
import { groupResults, outcomeOf } from "../../optimize_rollout.js";
import { gateLabel } from "../../optimize_gate_label.js";
import Fact from "./Fact.jsx";
import ReflectorIO from "./ReflectorIO.jsx";
import StageCalls from "./StageCalls.jsx";

// Part 1: one step, one split.
//
// Deliberately *not* the evaluation three-column page. That page answers "which
// questions failed"; this one answers "what did the optimizer see, and what did
// it conclude" — so the list is grouped by analyst call rather than by verdict,
// and the right-hand pane shows either a minibatch or a question depending on
// what was clicked.
//
// The trace viewer is the exception: `SpanList` and `SpanDetail` are reused
// unchanged, because a trace looked at here and a trace looked at in Evaluation
// have to be the same object for the two to be comparable at all.

// The five outcomes, as icons rather than as text characters.
//
// They used to be the literal glyphs ✓ ◐ ✗ ⚠ ·, and four of the five were fine.
// `⚠` (U+26A0) is not in Inter, so it fell through to the system emoji font and
// rendered at 26×45.5px next to four 18×19.5px siblings — one failed question
// added 26px to its row and threw the whole list out of alignment. Which font
// answers for a glyph is not something a stylesheet can settle, so the mark is
// an icon now, sized by us. `ScriptRunPanel` had already made this call for the
// same reason; this was the last place still using characters.
//
// `label` is not decoration either. `◐` is the mark people ask about — it means
// the judge gave partial credit to an answer it still called wrong, which is
// precisely the gap between the hard and soft metrics — and nothing on the page
// said so. Every mark now carries its meaning in a tooltip and in the
// accessibility tree, and the list header spells the ambiguous ones out.
const OUTCOME = {
  correct: { Icon: IconCheck, tone: "success", label: "correct" },
  partial: {
    Icon: IconHalfCircle,
    tone: "warning",
    label: "partial credit — judged wrong, but scored above zero",
  },
  incorrect: { Icon: IconX, tone: "danger", label: "incorrect" },
  error: { Icon: IconAlert, tone: "neutral", label: "never produced a score" },
  pending: { Icon: IconDot, tone: "neutral", label: "not answered yet" },
};

// One mark, at a fixed size whatever the outcome. The wrapper carries the colour
// and the name so the icon itself stays a plain shape.
function OutcomeMark({ outcome }) {
  const { Icon, label } = OUTCOME[outcome];
  return (
    // `role="img"` with a name, rather than a hidden text twin: the icon *is*
    // the content of this cell, and without a role a bare labelled span is not
    // reliably announced at all.
    <span
      className={`opt-mark ${outcome}`}
      role="img"
      aria-label={label}
      title={label}
    >
      <Icon size={14} />
    </span>
  );
}

export default function RolloutDetail({ runId, stepNo, split, onBack, onPickSplit, onOpenSkill }) {
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState(null);
  const [selection, setSelection] = useState(null);
  const [trace, setTrace] = useState(null);
  const [traceLoading, setTraceLoading] = useState(false);
  const [activeSpan, setActiveSpan] = useState(null);

  useEffect(() => {
    setDetail(null);
    setError(null);
    setSelection(null);
    api
      .getRolloutDetail(runId, stepNo, split)
      .then((data) => {
        setDetail(data);
        // Open on the first analyst call rather than on nothing: the minibatch
        // is what this page is for, and an empty right-hand pane makes the
        // whole screen look like a list that failed to load.
        const groups = groupResults(data);
        const first = groups.find((g) => g.minibatch);
        if (first) setSelection({ kind: "minibatch", no: first.minibatch_no });
      })
      .catch((e) => setError(e.message));
  }, [runId, stepNo, split]);

  useEffect(() => {
    if (selection?.kind !== "question") {
      setTrace(null);
      return;
    }
    let live = true;
    setTraceLoading(true);
    setActiveSpan(null);
    api
      .getRolloutResultTrace(runId, stepNo, split, selection.id)
      .then((data) => live && setTrace(data))
      .catch((e) => live && setTrace({ trace_state: "error", trace_error: e.message, spans: [] }))
      .finally(() => live && setTraceLoading(false));
    return () => {
      live = false;
    };
  }, [runId, stepNo, split, selection]);

  if (error) return <Banner tone="error" title="Could not load this rollout">{error}</Banner>;
  if (!detail) return <Skeleton variant="row" count={6} />;

  const groups = groupResults(detail);
  const selectedResult =
    selection?.kind === "question"
      ? detail.results.find((r) => r.id === selection.id)
      : null;
  const selectedBatch =
    selection?.kind === "minibatch"
      ? detail.minibatches.find((m) => m.minibatch_no === selection.no)
      : null;

  return (
    <div className="opt-rollout">
      <Card>
        <CardHeader
          title={`${detail.step_no === 0 ? "Baseline" : `Step ${detail.step_no}`} in detail`}
          actions={
            <>
              {/* Which skill was rolled out, not which step ran it. On training
                  these differ — the step measures the skill it inherited, then
                  edits it — and a header that did not say so would make the two
                  rollouts of a step look like a repeat measurement.

                  Up here with the title rather than down among the figures: it
                  is what the whole card is about, and standing in the metric row
                  it competed with four numbers for the same glance. */}
              <Badge tone="info" size="sm">
                skill from step {detail.skill_step_no}
                {split === "train" &&
                  detail.skill_step_no !== detail.step_no &&
                  " (before this step's edits)"}
              </Badge>
              <Button variant="ghost" icon={<IconArrowLeft size={15} />} onClick={onBack}>
                Back to the run
              </Button>
            </>
          }
        />

        {/* Both splits, from one page.
            This page only ever showed the split in its URL, and the step card
            that led here offered a link per split — so the validation half was
            unreachable on exactly the steps that skipped it, and reaching it on
            the others meant going back to the chart. A step measures two things;
            this is the page about that step.

            The tab changes the route rather than local state, deliberately: the
            address is one a developer sends to a colleague, and it should name
            what they will see. */}
        <div className="opt-splittabs" role="tablist" aria-label="Which split to show">
          <button
            type="button"
            role="tab"
            aria-selected={split === "train"}
            className={split === "train" ? "opt-splittab is-on" : "opt-splittab"}
            disabled={detail.step_no === 0}
            title={detail.step_no === 0
              ? "The baseline buys no training rollout — there was no candidate to train on yet"
              : undefined}
            onClick={() => onPickSplit?.("train")}
          >
            Training
            <span className="muted"> · answered, then reflected on</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={split === "val"}
            className={split === "val" ? "opt-splittab is-on" : "opt-splittab"}
            disabled={!detail.val_rolled_out}
            title={detail.val_rolled_out
              ? undefined
              : "This step changed nothing, so its candidate was identical to a skill already scored and no validation rollout was bought"}
            onClick={() => onPickSplit?.("val")}
          >
            Validation
            <span className="muted">
              {detail.val_rolled_out ? " · held back, and what the gate judged" : " · skipped"}
            </span>
          </button>
        </div>
        {/* The four figures this rollout produced, as a row of labelled figures
            rather than as a sentence.
            They used to be one wrapping line of same-sized grey text — a badge,
            then `accuracy 88%`, then a three-part latency string that ate half
            the width, then `activation 100%`, then `8 of 8 scored` — with no
            labels and no hierarchy, so finding any one of them meant reading all
            of them. The grid is the same one the run header above uses, which is
            the point: two pages in this section both open on "the numbers for
            the thing you just clicked", and they should not say it two ways. */}
        <dl className="opt-runfacts">
          <Fact
            label="Accuracy"
            value={pct(detail.hard)}
            sub="hard verdicts"
            title="The share of scored questions the judge called correct"
          />
          {/* Average first, because that is the figure people quote at each
              other. The other two are what say whether it means anything: an
              average well above the median is one question that hung. */}
          <Fact
            label="Latency"
            value={secs(detail.latency_mean_ms)}
            sub={`avg · median ${secs(detail.latency_p50_ms)} · max ${secs(detail.latency_max_ms)}`}
            title="How long the agent took to answer one question"
          />
          <Fact
            label="Activation"
            value={pct(detail.activation_rate)}
            sub={activationSub(detail)}
            title="How often the agent was actually seen reading this skill"
          />
          {/* The denominator, as a figure of its own. A rollout that quietly
              measured fewer questions than it set out to is the thing worth
              noticing, and it used to be said twice — once here in grey and
              again underneath as a warning banner, which is one fact wearing two
              colours. */}
          <Fact
            label="Scored"
            value={`${detail.n_scored} of ${detail.n_items}`}
            sub={scoredSub(detail)}
            title="Questions that produced a verdict. Failures are excluded from every figure here rather than counted wrong."
          />
        </dl>
        {detail.aborted && (
          <Banner tone="error" title="This rollout was abandoned">
            {detail.abort_reason || "too much of it failed to be worth scoring"}
          </Banner>
        )}
        {split === "val" && detail.gate_action && (
          <Banner
            tone={detail.gate_action === "reject" ? "warning" : "success"}
            title={gateLabel(detail).detail}
          >
            {/* The numbers above are what the gate compared. The analyst's
                sentence about its patch used to be dropped in here as the
                banner's whole body, unattributed, so it read as the gate's
                explanation of its own verdict — which it is not.
                A candidate dropped because its split never came back was not
                compared with anything, so this sentence would be a lie there. */}
            {detail.gate_reject_reason === "val_errors"
              ? "The gate was never asked: without a trustworthy accuracy there is nothing to compare against the best score so far."
              : "These are the numbers the gate compared: this candidate's accuracy on the held-back questions, against the best a candidate has scored so far."}
            {detail.edit_summary && (
              <div className="ui-banner-note">
                The analyst described its patch as: “{detail.edit_summary}”
              </div>
            )}
          </Banner>
        )}

        {/* The step's own stages, not any minibatch's — which is why they are
            here and not in the list beside the analyst calls. One merge and one
            ranking serve the whole step, and a row labelled like a minibatch
            would claim an analyst saw something it did not. */}
        {split === "train" && (
          <StageCalls
            stageCalls={detail.stage_calls}
            nApplied={detail.n_edits_applied}
            nSkipped={detail.n_edits_skipped}
            editSummary={detail.edit_summary}
          />
        )}
      </Card>

      <div className="opt-rollout-body">
        <Card padded={false} className="opt-rollout-list">
          <CardHeader
            title={split === "train" ? "By analyst call" : "Questions"}
            count={detail.results.length}
          />
          {/* Only the two marks that need explaining. A tick and a cross are not
              a legend anyone reads; `◐` and `⚠` are the two people stop on, and
              the half-circle is the one that decides whether the soft metric
              above makes sense. Rendered from the same OUTCOME table as the rows
              so a mark can never mean one thing here and another there. */}
          <Legend outcomes={legendFor(detail.results)} />
          <div className="opt-rollout-groups">
            {groups.map((group) => (
              <Group
                key={group.minibatch_no ?? "none"}
                group={group}
                split={split}
                selection={selection}
                onSelect={setSelection}
              />
            ))}
          </div>
        </Card>

        <div className="opt-rollout-pane">
          {selectedBatch && (
            <ReflectorIO
              minibatch={selectedBatch}
              // What became of the patch this analyst proposed. The pane showed
              // what was asked for and stopped there, so "did any of this land?"
              // — the only question the proposal raises — needed another page.
              editReports={detail.edit_reports}
              nApplied={detail.n_edits_applied}
              nSkipped={detail.n_edits_skipped}
              onOpenSkill={onOpenSkill}
            />
          )}
          {selectedResult && (
            <QuestionPane
              result={selectedResult}
              trace={trace}
              loading={traceLoading}
              activeSpan={activeSpan}
              onPickSpan={setActiveSpan}
            />
          )}
          {!selectedBatch && !selectedResult && (
            <p className="opt-hint">
              {split === "train"
                ? "Pick a question, or an analyst call, from the list."
                : "Pick a question from the list to see how the agent answered it."}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

// What the activation figure is a share *of*.
//
// The rate counts only the questions where activation could be observed at all:
// a trace that never landed, or one showing no skill read by an agent we have
// not established reads skills visibly, is unknown rather than a no — and
// unknown is left out of the fraction rather than averaged in as a zero. That is
// the right call and it was invisible, so `100%` read as "all ten questions" when
// it could equally have been "the nine we could see, and one we could not".
//
// Counted from the rows rather than taken from the summary because the rows are
// already here and carry `activated` each — one number derived in one place
// cannot disagree with the list underneath it.
function activationSub(detail) {
  const results = detail.results || [];
  const observed = results.filter((r) => r.activated != null).length;
  if (!observed) return "not observable on this agent";
  const unknown = results.length - observed;
  return unknown
    ? `of ${observed} observed · ${unknown} unknown`
    : `of ${observed} observed`;
}

// The failures behind a short denominator, named. They are excluded from every
// figure beside this one rather than counted wrong — an agent timeout is not the
// skill being incorrect — so the exclusion has to be legible or the accuracy
// above it is a number about a batch nobody can see the edges of.
function scoredSub(detail) {
  const parts = [];
  if (detail.n_agent_error) parts.push(`${detail.n_agent_error} agent`);
  if (detail.n_judge_error) parts.push(`${detail.n_judge_error} judge`);
  if (!parts.length) return "all questions scored";
  return `${parts.join(" · ")} excluded`;
}

// Which marks this rollout actually contains, of the two worth spelling out.
// Explaining `partial` on a list with no partial answers in it is noise, and a
// legend that is noise on most pages stops being read on the pages it matters.
function legendFor(results) {
  const present = new Set((results || []).map(outcomeOf));
  return ["partial", "error"].filter((outcome) => present.has(outcome));
}

function Legend({ outcomes }) {
  if (!outcomes.length) return null;
  return (
    <p className="opt-legend">
      {outcomes.map((outcome) => (
        <span key={outcome} className="opt-legend-item">
          <OutcomeMark outcome={outcome} />
          {OUTCOME[outcome].label}
        </span>
      ))}
    </p>
  );
}

function Group({ group, split, selection, onSelect }) {
  const { minibatch, counts } = group;
  const selected =
    selection?.kind === "minibatch" && selection.no === group.minibatch_no;
  return (
    <div className="opt-rollout-group">
      {minibatch ? (
        <button
          type="button"
          className={selected ? "opt-rollout-group-head selected" : "opt-rollout-group-head"}
          onClick={() => onSelect({ kind: "minibatch", no: minibatch.minibatch_no })}
        >
          <strong>Minibatch {minibatch.minibatch_no}</strong>
          <span className="muted">
            {minibatch.n_items} items · {counts.incorrect + counts.partial} failed
          </span>
          {minibatch.error && <Badge tone="danger" size="sm">analyst failed</Badge>}
        </button>
      ) : (
        split === "train" && (
          <div className="opt-rollout-group-head static">
            <strong>Not reflected on</strong>
            <span className="muted">no analyst saw these</span>
          </div>
        )
      )}
      <ul className="opt-qlist">
        {group.results.map((result) => {
          const outcome = outcomeOf(result);
          const active = selection?.kind === "question" && selection.id === result.id;
          return (
            <li key={result.id}>
              <button
                type="button"
                className={active ? "opt-qrow selected" : "opt-qrow"}
                onClick={() => onSelect({ kind: "question", id: result.id })}
              >
                <OutcomeMark outcome={outcome} />
                <span className="opt-qtext">{result.question || result.item_key}</span>
                <span className="opt-qscore">
                  {outcome === "error"
                    ? result.failure_kind || "error"
                    : result.judge_score == null
                      ? "—"
                      : result.judge_score.toFixed(2)}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function QuestionPane({ result, trace, loading, activeSpan, onPickSpan }) {
  // `SpanList` reports the span it was clicked on by **index**, and `SpanDetail`
  // takes the span **object**. Handing the number straight across read
  // plausibly and threw on `span.token_usage` the moment anyone clicked a step
  // — with no error boundary above it, the whole app went white. Evaluation
  // (`RunDetail.jsx`) and the playground both do this lookup; this page is the
  // one that skipped it.
  const activeSpanObj = trace?.spans?.find((s) => s.index === activeSpan) || null;
  const suspectByIndex = {};
  (trace?.analysis?.suspects || []).forEach((s) => (suspectByIndex[s.span_index] = s));

  return (
    <div className="opt-question">
      <div className="opt-question-head">
        <Badge tone={OUTCOME[outcomeOf(result)].tone} size="sm">
          {result.verdict || result.status}
        </Badge>
        {result.activated === false && (
          <Badge tone="warning" size="sm" title="Neither detector saw this skill being read.">
            skill not read
          </Badge>
        )}
        {result.activated == null && (
          <Badge tone="neutral" size="sm" title="The detectors could not tell — which is not the same as 'no'.">
            activation unknown
          </Badge>
        )}
        {result.skills_read?.length > 0 && (
          <span className="muted">read: {result.skills_read.join(", ")}</span>
        )}
      </div>

      {result.error_message && (
        <Banner tone="error" title={result.failure_kind || "This question failed"}>
          {result.error_message}
        </Banner>
      )}

      <div className="opt-question-cols">
        {loading && !trace ? (
          <Skeleton variant="row" count={4} />
        ) : (
          <>
            {/* `question` is a string, because that is what `SpanList` renders
                it as. This passed `{question, ground_truth_response}` — an
                object handed to React as a child, which throws, and with no
                error boundary above it took the whole app down to a white page
                every time a question in a rollout was clicked. The expected
                answer was never this prop's job: `SpanList` reads it off the
                trace, which carries it. */}
            <SpanList
              trace={trace}
              activeSpan={activeSpan}
              onPickSpan={onPickSpan}
              canReDiagnose={false}
              question={result.question}
              emptyHint="No trace for this question."
            />
            <SpanDetail
              span={activeSpanObj}
              suspect={activeSpanObj ? suspectByIndex[activeSpanObj.index] : null}
            />
          </>
        )}
      </div>
    </div>
  );
}

function pct(value) {
  return value == null ? "—" : `${Math.round(value * 100)}%`;
}

