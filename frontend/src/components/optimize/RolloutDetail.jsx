import React, { useEffect, useState } from "react";
import { api } from "../../api.js";
import Badge from "../ui/Badge.jsx";
import Banner from "../ui/Banner.jsx";
import Button from "../ui/Button.jsx";
import Card, { CardHeader } from "../ui/Card.jsx";
import Skeleton from "../ui/Skeleton.jsx";
import SpanList from "../SpanList.jsx";
import SpanDetail from "../SpanDetail.jsx";
import { IconArrowLeft } from "../icons.jsx";
import { groupResults, outcomeOf } from "../../optimize_rollout.js";
import ReflectorIO from "./ReflectorIO.jsx";

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

const OUTCOME = {
  correct: { mark: "✓", tone: "success" },
  partial: { mark: "◐", tone: "warning" },
  incorrect: { mark: "✗", tone: "danger" },
  error: { mark: "⚠", tone: "neutral" },
  pending: { mark: "·", tone: "neutral" },
};

export default function RolloutDetail({ runId, stepNo, split, onBack }) {
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
          title={`${detail.step_no === 0 ? "Baseline" : `Step ${detail.step_no}`} · ${
            split === "train" ? "training" : "validation"
          }`}
          actions={
            <Button variant="ghost" icon={<IconArrowLeft size={15} />} onClick={onBack}>
              Back to the run
            </Button>
          }
        />
        <div className="opt-run-meta">
          {/* Which skill was rolled out, not which step ran it. On training
              these differ — the step measures the skill it inherited, then
              edits it — and a header that did not say so would make the two
              rollouts of a step look like a repeat measurement. */}
          <Badge tone="info" size="sm">
            skill from step {detail.skill_step_no}
            {split === "train" && detail.skill_step_no !== detail.step_no && " (before this step's edits)"}
          </Badge>
          <span>hard {pct(detail.hard)}</span>
          <span>soft {pct(detail.soft)}</span>
          <span>
            latency {secs(detail.latency_min_ms)} / {secs(detail.latency_p50_ms)} /{" "}
            {secs(detail.latency_max_ms)}
          </span>
          <span>activation {pct(detail.activation_rate)}</span>
          <span>
            {detail.n_scored} of {detail.n_items} scored
          </span>
        </div>

        {(detail.n_agent_error > 0 || detail.n_judge_error > 0) && (
          <Banner tone="warning" title="Some questions never produced a score">
            {detail.n_agent_error} agent · {detail.n_judge_error} judge. These are
            excluded from every figure above — they are not counted wrong — but
            they are still listed below, because a rollout that quietly measured
            fewer questions than it set out to is the thing worth noticing.
          </Banner>
        )}
        {detail.aborted && (
          <Banner tone="error" title="This rollout was abandoned">
            {detail.abort_reason || "too much of it failed to be worth scoring"}
          </Banner>
        )}
        {split === "val" && detail.gate_action && (
          <Banner
            tone={detail.gate_action === "reject" ? "warning" : "success"}
            title={
              detail.gate_action === "reject"
                ? `The gate rejected this candidate (${detail.gate_reject_reason})`
                : `The gate accepted this candidate (${detail.gate_action.replace(/_/g, " ")})`
            }
          >
            {detail.edit_summary || "These are the numbers the gate compared."}
          </Banner>
        )}
      </Card>

      <div className="opt-rollout-body">
        <Card padded={false} className="opt-rollout-list">
          <CardHeader
            title={split === "train" ? "By analyst call" : "Questions"}
            count={detail.results.length}
          />
          <div className="opt-groups">
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
          {selectedBatch && <ReflectorIO minibatch={selectedBatch} />}
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
              Pick a question, or an analyst call, from the list.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

function Group({ group, split, selection, onSelect }) {
  const { minibatch, counts } = group;
  const selected =
    selection?.kind === "minibatch" && selection.no === group.minibatch_no;
  return (
    <div className="opt-group">
      {minibatch ? (
        <button
          type="button"
          className={selected ? "opt-group-head selected" : "opt-group-head"}
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
          <div className="opt-group-head static">
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
                <span className={`opt-mark ${outcome}`}>{OUTCOME[outcome].mark}</span>
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
            <SpanList
              trace={trace}
              activeSpan={activeSpan}
              onPickSpan={onPickSpan}
              canReDiagnose={false}
              question={{
                question: result.question,
                ground_truth_response: result.ground_truth_response,
              }}
              emptyHint="No trace for this question."
            />
            <SpanDetail span={activeSpan} />
          </>
        )}
      </div>
    </div>
  );
}

function pct(value) {
  return value == null ? "—" : `${Math.round(value * 100)}%`;
}

function secs(ms) {
  return ms == null ? "—" : `${(ms / 1000).toFixed(1)}s`;
}
