import React from "react";
import Badge from "../ui/Badge.jsx";
import Button from "../ui/Button.jsx";
import { IconChevronRight, IconDownload, IconX } from "../icons.jsx";
import { plural } from "../../plural.js";

// One step, pinned by clicking it on the chart. This is the half of the chart
// that carries actions — pinning rather than hovering exists so that the
// buttons are reachable by a pointer that has to travel to them and by a
// keyboard that cannot hover at all.
//
// Two halves, mirroring the two things a step does:
//
//   Left — what the skill scored, and on what. As a table with a column per
//     split, because train and validation are the two measurements a step makes
//     and every question a reader has about them is a comparison: was the drop
//     on training or on validation, was one of them slower, did one of them see
//     the errors. As a flat list of "83% hard · 82% soft" rows those comparisons
//     had to be made in the reader's head, and `soft` was in the way — the
//     hard/soft toggle above the chart already switches the whole page between
//     the two, so printing both here said the number twice and answered neither
//     question.
//   Right — what the step did to the skill, and whether the gate kept it.
//
// The two buttons are named as a pair and sit on one line: `View details` opens
// the rollouts, `View skill diff` opens the edit. They used to be three links
// with sentence-shaped labels ("Training questions & analyst calls") that
// appeared and disappeared depending on which splits had run.

export default function StepCard({
  step, run, onClose, onDownload, downloading, onOpenRollout, onOpenSkill,
}) {
  const isBaseline = step.step_no === 0;
  const isBest = run.best_step === step.step_no;
  // Validation is skipped when the step's candidate is byte-identical to a skill
  // that has already been scored — every edit was refused, so there is nothing
  // new to measure. That is worth saying in words: as a row of em-dashes it
  // looked like a measurement that had failed.
  const valSkipped = !isBaseline && step.val_n_items == null;

  return (
    <div className="opt-stepcard">
      <div className="opt-stepcard-head">
        <strong>{isBaseline ? "Baseline — the skill as it arrived" : `Step ${step.step_no}`}</strong>
        {!isBaseline && <Badge size="sm">epoch {step.epoch_no}</Badge>}
        {isBest && <Badge tone="success" size="sm">best by validation</Badge>}
        {step.status === "aborted" && (
          <Badge tone="warning" size="sm">stopped ({step.abort_reason || "interrupted"})</Badge>
        )}
        {step.retried && <Badge tone="warning" size="sm">rolled out twice</Badge>}
        {/* "score reused" meant nothing to a reader who did not already know
            about the engine's skill-hash cache. What it is *about* is that the
            step changed nothing, which is both the cause and the thing worth
            knowing. */}
        {step.candidate_from_cache && (
          <Badge
            tone="info"
            size="sm"
            title="Every edit this step proposed was refused, so the skill it produced is identical to the one before it — and the score of that skill was already known. No validation rollout was bought."
          >
            no change · score reused
          </Badge>
        )}
        <span className="opt-stepcard-spacer" />
        <Button
          variant="ghost"
          icon={<IconDownload size={14} />}
          loading={downloading}
          onClick={() => onDownload(step.step_no)}
          title="Download this step's skill directory as a zip"
        >
          Download this skill
        </Button>
        {/* Icon-only, so it needs a name of its own — `title` is a tooltip, not
            an accessible name, and a screen reader would announce "button". */}
        <Button
          variant="ghost"
          icon={<IconX size={14} />}
          onClick={onClose}
          title="Unpin this step"
          aria-label="Unpin this step"
        />
      </div>

      <div className="opt-stepcard-body">
        <section>
          <h4>Measured</h4>
          <SplitTable step={step} isBaseline={isBaseline} valSkipped={valSkipped} />
          {valSkipped && (
            <p className="opt-hint">
              No validation rollout was bought for this step: every edit it
              proposed was refused, so the skill it produced is identical to the
              one it started from and that skill's score was already known.
            </p>
          )}
          <div className="opt-stepcard-links">
            <Button
              variant="secondary"
              size="sm"
              iconRight={<IconChevronRight size={14} />}
              onClick={() => onOpenRollout(step.step_no, isBaseline ? "val" : "train")}
            >
              View details
            </Button>
          </div>
        </section>

        <section>
          <h4>Changed</h4>
          {isBaseline ? (
            <p className="opt-hint">
              Step 0 measures the skill you started with. Nothing has been edited
              at this point — it is the line every later step is compared against.
            </p>
          ) : (
            <>
              <Row
                label="Lines"
                value={step.lines_added == null ? "—" : `+${step.lines_added} / −${step.lines_removed}`}
                sub={step.files_touched ? `across ${plural(step.files_touched, "file")}` : null}
              />
              <Row
                label="Edits"
                value={step.n_edits_applied == null ? "—" : `${step.n_edits_applied} applied`}
                sub={step.n_edits_skipped ? `${step.n_edits_skipped} could not be applied` : null}
                tone={step.n_edits_skipped ? "warning" : null}
              />
              <Row label="Gate" value={<GateVerdict step={step} />} />
              {/* Attributed. This is the analyst's own sentence about its patch,
                  and unlabelled it read as the page talking — which is how a
                  string beginning "fake merge:" ended up looking like a bug
                  report rather than like the stand-in model's reasoning. */}
              {step.edit_summary && (
                <div className="opt-rationale">
                  <span className="opt-rationale-label">Analyst's rationale</span>
                  <p>{step.edit_summary}</p>
                </div>
              )}
              {/* Offered whether or not the gate kept the edits: reading what
                  was turned down is how "the idea was bad" gets told apart
                  from "the rollout was noisy". */}
              <div className="opt-stepcard-links">
                <Button
                  variant="secondary"
                  size="sm"
                  iconRight={<IconChevronRight size={14} />}
                  onClick={() => onOpenSkill(step.step_no)}
                >
                  View skill diff
                </Button>
              </div>
            </>
          )}
        </section>
      </div>
    </div>
  );
}

// The two splits side by side. Every row is a question a reader asks as a
// comparison, so the comparison is the layout rather than something they do in
// their head from two separate lists.
function SplitTable({ step, isBaseline, valSkipped }) {
  const rows = [
    {
      label: "Questions",
      title: "How many questions the split holds — the denominator under every figure below",
      train: count(step.train_n_scored, step.train_n_items),
      val: count(step.val_n_scored, step.val_n_items),
    },
    {
      label: "Accuracy",
      title: "The share the judge scored strictly correct",
      train: pct(step.train_hard),
      val: pct(step.val_hard),
      strong: true,
    },
    {
      label: "Avg latency",
      title: "Mean time the agent took to answer one question",
      train: secs(step.train_latency_mean_ms),
      val: secs(step.val_latency_mean_ms),
    },
    {
      label: "Median latency",
      title: "The middle answer's time. Far below the average means a few slow questions carried it",
      train: secs(step.train_latency_p50_ms),
      val: secs(step.val_latency_p50_ms),
    },
    {
      label: "Min latency",
      train: secs(step.train_latency_min_ms),
      val: secs(step.val_latency_min_ms),
    },
    {
      label: "Max latency",
      title: "The slowest single answer — the one that would hit a timeout first",
      train: secs(step.train_latency_max_ms),
      val: secs(step.val_latency_max_ms),
    },
    {
      label: "Activation",
      title: "How often the agent was actually seen reading this skill",
      train: pct(step.train_activation_rate),
      val: pct(step.val_activation_rate),
    },
    {
      label: "System errors",
      title: "Questions that never produced a score. Excluded from every figure above — not counted wrong",
      train: errors(step.train_n_agent_error, step.train_n_judge_error),
      val: errors(step.val_n_agent_error, step.val_n_judge_error),
    },
  ];

  return (
    <table className="opt-splittable">
      <thead>
        <tr>
          <th />
          {/* The baseline buys no training rollout — there was no candidate to
              train on yet — so its train column is not empty, it is absent. */}
          <th className="num">Train{isBaseline && <span className="muted"> —</span>}</th>
          <th className="num">Validation</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.label} className={row.strong ? "is-strong" : undefined}>
            <th scope="row" title={row.title}>{row.label}</th>
            <td className="num">{isBaseline ? "—" : row.train}</td>
            <td className="num">{valSkipped ? "skipped" : row.val}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function GateVerdict({ step }) {
  if (!step.gate_action) return <span className="muted">not judged</span>;
  if (step.gate_action === "reject") {
    return (
      <span>
        <Badge tone="neutral" size="sm">rejected</Badge>{" "}
        {step.gate_reject_reason === "activation"
          ? "the agent stopped reading the skill"
          : "it did not beat the current skill on validation"}
      </span>
    );
  }
  return (
    <span>
      <Badge tone="success" size="sm">{step.gate_action.replace(/_/g, " ")}</Badge>{" "}
      {step.gate_action === "accept_new_best" ? "a new best" : "kept, but not a new best"}
    </span>
  );
}

function Row({ label, value, sub, tone }) {
  return (
    <div className={tone ? `opt-stepcard-row ${tone}` : "opt-stepcard-row"}>
      <span className="opt-stepcard-label">{label}</span>
      <span className="opt-stepcard-value">{value}</span>
      {sub && <span className="opt-stepcard-sub">{sub}</span>}
    </div>
  );
}

// Naming the denominator matters: "80%" over 5 questions and over 50 are the
// same number and completely different evidence.
function count(scored, total) {
  if (total == null) return "—";
  return scored === total ? `${total}` : `${scored} of ${total}`;
}

function errors(agent, judge) {
  const total = (agent || 0) + (judge || 0);
  if (agent == null && judge == null) return "—";
  return total ? `${total}` : "none";
}

function secs(ms) {
  return ms == null ? "—" : `${(ms / 1000).toFixed(1)}s`;
}

function pct(value) {
  return value == null ? "—" : `${Math.round(value * 100)}%`;
}
