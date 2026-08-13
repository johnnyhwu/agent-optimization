import React from "react";
import Badge from "../ui/Badge.jsx";
import Button from "../ui/Button.jsx";
import { IconChevronRight, IconDownload, IconX } from "../icons.jsx";

// One step, pinned by clicking it on the chart. This is the half of the chart
// that carries actions — pinning rather than hovering exists so that the
// buttons are reachable by a pointer that has to travel to them and by a
// keyboard that cannot hover at all.
//
// Two halves, mirroring the two things a step does:
//
//   Part 1 — what the skill scored, and on what. The system errors and the
//     excluded count are here rather than tucked away because every figure
//     above them is computed over the questions that *did* run, and the size of
//     that exclusion is the first thing that makes an accuracy suspicious.
//   Part 2 — what the step did to the skill, and whether the gate kept it.

export default function StepCard({ step, run, onClose, onDownload, downloading, onOpenRollout }) {
  const isBaseline = step.step_no === 0;
  const isBest = run.best_step === step.step_no;
  const errors = (step.val_n_agent_error || 0) + (step.val_n_judge_error || 0)
    + (step.train_n_agent_error || 0) + (step.train_n_judge_error || 0);

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
        {step.candidate_from_cache && (
          <Badge tone="info" size="sm" title="An identical skill had already been scored, so this step reused that score instead of paying for the rollout again.">
            score reused
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
          <Row label="Validation" value={`${pct(step.val_hard)} hard · ${pct(step.val_soft)} soft`}
               sub={counted(step.val_n_scored, step.val_n_items)} />
          {!isBaseline && (
            <Row label="Train" value={`${pct(step.train_hard)} hard · ${pct(step.train_soft)} soft`}
                 sub={counted(step.train_n_scored, step.train_n_items)} />
          )}
          <Row label="Latency" value={latency(step)} />
          <Row
            label="Activation"
            value={pct(step.val_activation_rate)}
            sub="how often the agent was seen reading this skill"
          />
          <Row
            label="System errors"
            value={errors ? `${errors}` : "none"}
            sub={errors ? "excluded from every figure above, not counted wrong" : null}
            tone={errors ? "warning" : null}
          />
          {/* Only offered for a split that was actually rolled out. Step 0 has
              no training rollout — there was no candidate to train on yet — and
              a button leading to a 404 is worse than no button. */}
          <div className="opt-stepcard-links">
            {step.val_n_items != null && (
              <Button variant="ghost" icon={<IconChevronRight size={14} />}
                      onClick={() => onOpenRollout(step.step_no, "val")}>
                Validation questions
              </Button>
            )}
            {!isBaseline && step.train_n_items != null && (
              <Button variant="ghost" icon={<IconChevronRight size={14} />}
                      onClick={() => onOpenRollout(step.step_no, "train")}>
                Training questions & analyst calls
              </Button>
            )}
          </div>
        </section>

        <section>
          <h4>{isBaseline ? "No edits yet" : "Changed"}</h4>
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
                sub={step.files_touched ? `across ${step.files_touched} file(s)` : null}
              />
              <Row
                label="Edits"
                value={step.n_edits_applied == null ? "—" : `${step.n_edits_applied} applied`}
                sub={step.n_edits_skipped ? `${step.n_edits_skipped} could not be applied` : null}
                tone={step.n_edits_skipped ? "warning" : null}
              />
              <Row label="Gate" value={<GateVerdict step={step} />} />
              {step.edit_summary && (
                <p className="opt-stepcard-summary">{step.edit_summary}</p>
              )}
            </>
          )}
        </section>
      </div>
    </div>
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

function counted(scored, total) {
  if (scored == null || total == null) return null;
  // Naming the denominator matters: "80%" over 5 questions and over 50 are the
  // same number and completely different evidence.
  return scored === total ? `${total} questions` : `${scored} of ${total} scored`;
}

function latency(step) {
  const [min, p50, max] = [
    step.val_latency_min_ms, step.val_latency_p50_ms, step.val_latency_max_ms,
  ];
  if (p50 == null) return "—";
  return `${secs(min)} / ${secs(p50)} / ${secs(max)}`;
}

function secs(ms) {
  return ms == null ? "—" : `${(ms / 1000).toFixed(1)}s`;
}

function pct(value) {
  return value == null ? "—" : `${Math.round(value * 100)}%`;
}
