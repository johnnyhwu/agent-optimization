import React from "react";
import Badge from "../ui/Badge.jsx";
import Button, { IconButton } from "../ui/Button.jsx";
import RunDuration from "./RunDuration.jsx";
import RunNameEditor from "../RunNameEditor.jsx";
import Fact from "./Fact.jsx";
import { IconDownload, IconPlay, IconRefresh, IconStop, IconTrash } from "../icons.jsx";
import { STATUS_TONE } from "./RunList.jsx";
import { STEP_PHASES } from "../../optimize_steps.js";
import { runStartedAt } from "../../optimize_run_label.js";
import { formatSpan, runDuration } from "../../optimize_duration.js";
import { plural } from "../../plural.js";

// The run, at the top of its own page.
//
// What was here before was a `CardHeader` and a single row of bare spans:
//
//     running  billing  isolated  14 train · 6 validation  10/10 steps
//
// Three problems, and they compound. The title used the card-header style —
// muted, uppercase, caption-sized — so the run's name looked like a section
// label rather than the name of the thing on screen, and the filled red Stop
// button beside it was by far the heaviest element in the panel. The meta row
// ran fixed facts and live ones together in one undifferentiated line, so
// nothing distinguished "this run trains on 14 questions", which will never
// change, from "10 of 10 steps", which changes all afternoon. And the live half
// was the half with no room: one caption, "step 3 · rollout", for the several
// minutes a rollout takes.
//
// So: a real title, the actions weighted by what the run's state makes worth
// doing, and the meta split in two — **Setup**, which is what you configured and
// is worth checking once, and **Progress**, which is what is happening and is
// worth watching. The stage strip is the part that was missing entirely: which
// of a step's five stages the run is in, and for the two that answer questions,
// how many of them are done.
export default function RunHeader({
  run, activity, progress, steps, isMine, busy, downloading,
  onRename, onStop, onResume, onRefresh, onDownloadBest, onDelete,
}) {
  const running = run.status === "running" || run.status === "pending";
  const cancelling = running && run.cancel_requested;

  return (
    <div className="opt-runhead">
      <div className="opt-runhead-top">
        <div className="opt-runhead-title">
          {/* An editable name, not a section label. Most runs are started
              without one — the wizard offers its suggestion as a placeholder —
              and until now the only chance to name a run was before it had done
              anything worth naming it after. */}
          <h2>
            <RunNameEditor
              name={run.name}
              fallback={`Optimizing ${run.skill_name}`}
              canEdit={isMine}
              onRename={onRename}
              label="run"
            />
          </h2>
          <Badge tone={STATUS_TONE[run.status] || "neutral"}>
            {cancelling ? "stopping" : run.status}
          </Badge>
        </div>

        <div className="opt-runhead-actions">
          {/* Weighted by what the run's state makes worth doing. On a finished
              run the skill is the reason it was run at all, so that is the
              primary; while it is running there is nothing to download yet and
              the only real action is to stop. Stop is secondary with danger
              text rather than a filled red block: it is one button on a page
              whose subject is a chart, and it was drawing every eye. */}
          {run.best_step != null && (
            <Button
              variant={running ? "secondary" : "primary"}
              icon={<IconDownload size={15} />}
              loading={downloading === "best"}
              onClick={onDownloadBest}
              title={`Step ${run.best_step}, the best this run scored on validation`}
            >
              Download best skill
            </Button>
          )}
          {running && isMine && (
            <Button
              variant="secondary"
              className="is-danger-text"
              icon={<IconStop size={15} />}
              loading={busy}
              disabled={cancelling}
              onClick={onStop}
            >
              {cancelling ? "Stopping…" : "Stop"}
            </Button>
          )}
          {/* Resume is offered only for `interrupted`: a cancelled run was a
              decision and a failed one stopped because continuing would produce
              a misleading result. Both stay available as the starting point for
              a new run instead. */}
          {run.status === "interrupted" && isMine && (
            <Button variant="primary" icon={<IconPlay size={15} />} loading={busy} onClick={onResume}>
              Resume
            </Button>
          )}
          {/* Icon-only. It is a repair for a stream that fell behind, not
              something anyone came here to do, and as a labelled button it had
              the same visual weight as stopping the run. */}
          <IconButton
            icon={<IconRefresh size={15} />}
            onClick={onRefresh}
            label="Reload this run from the server"
          />
          {/* Icon-only for the opposite reason: this one is irreversible, and a
              labelled red button beside the chart would be the first thing the
              eye lands on every time the page opens. Offered only to the
              developer who started the run — everyone who shares its source
              eval sets can read it — and only once it has stopped, because the
              server refuses to delete a live run and a button that always
              errors is worse than one that is not there. Stop first, then
              delete; that is the same order the API enforces. */}
          {isMine && !running && (
            <IconButton
              icon={<IconTrash size={15} />}
              className="ui-btn-destructive-hover"
              onClick={onDelete}
              label="Delete this run"
            />
          )}
        </div>
      </div>

      {/* Fixed facts. What this run was set up to do — true from the moment it
          was created and true after it finishes, which is exactly why it does
          not belong in the same line as the numbers that move. */}
      <dl className="opt-runfacts">
        <Fact label="Skill" value={<code>{run.skill_name}</code>} sub={run.mode} />
        <Fact label="Train" value={run.n_train} sub="questions learned from" />
        <Fact label="Validation" value={run.n_val} sub="questions held back" />
        <Fact
          label="Schedule"
          value={`${run.num_epochs} × ${run.steps_per_epoch}`}
          sub={`${plural(run.num_epochs, "epoch")} of ${plural(run.steps_per_epoch, "step")}`}
        />
        <Fact label="Batch" value={run.batch_size} sub="questions per step" />
        {/* When it started, and — the part that was missing entirely — how long
            it went on for. The two belong in one fact rather than in two: a run
            is an hour of paid agent calls, and "how long did that cost me" was
            not answerable anywhere on the page. `RunDuration` is its own
            component so that a live run's ticking second repaints these words
            and nothing else. */}
        <Fact
          label="Started"
          value={runStartedAt(run)}
          sub={<RunDuration run={run} steps={steps} by={run.created_by} />}
        />
      </dl>

      {/* Live state. Everything below this line changes while you watch it. */}
      <div className="opt-runprogress">
        <div className="opt-runprogress-bar">
          <div className="opt-runprogress-head">
            <span className="opt-runprogress-label">
              {progress.total == null
                ? `${progress.done} steps done`
                : `Step ${Math.min(progress.done + (running ? 1 : 0), progress.total)} of ${progress.total}`}
              {/* The baseline is a step here and in the rail, and it costs a
                  full validation rollout — but it is not one of the steps the
                  wizard quoted, so saying so avoids the "I asked for 9" moment. */}
              <span className="muted"> (baseline included)</span>
            </span>
            {run.best_step != null && run.best_score != null && (
              <Badge tone="success" mono size="sm">
                best: step {run.best_step} · {(run.best_score * 100).toFixed(0)}%
              </Badge>
            )}
          </div>
          <div className="opt-meter" role="progressbar"
               aria-valuenow={progress.done} aria-valuemin={0}
               aria-valuemax={progress.total ?? undefined}>
            <span
              className="opt-meter-fill"
              style={{ width: progress.total ? `${(progress.done / progress.total) * 100}%` : "0%" }}
            />
          </div>
        </div>

        {running && <PhaseStrip activity={activity} />}
        {!running && (
          <p className="opt-runprogress-done">
            {finishedSentence(run, steps)}
          </p>
        )}
      </div>
    </div>
  );
}

// Where the current step has got to. Five stages, in the order the engine
// performs them, with the two that answer questions carrying a count — those
// are the ones that take minutes, and they were the ones with nothing to show.
function PhaseStrip({ activity }) {
  if (!activity) {
    return <p className="opt-runprogress-waiting">Waiting for the run to report in…</p>;
  }
  const index = STEP_PHASES.findIndex((p) => p.key === activity.phase);
  const counted = activity.total != null && activity.done != null;

  return (
    <div className="opt-phases">
      <ol className="opt-phase-strip">
        {STEP_PHASES.map((phase, i) => {
          const state = i < index ? "is-done" : i === index ? "is-now" : "is-next";
          return (
            <li key={phase.key} className={`opt-phase ${state}`} title={phase.hint}>
              <span className="opt-phase-dot" aria-hidden="true" />
              <span className="opt-phase-label">{phase.label}</span>
            </li>
          );
        })}
      </ol>
      <p className="opt-phase-detail">
        <strong>
          {activity.stepNo === 0 ? "Baseline" : `Step ${activity.stepNo}`}
          {" · "}
          {/* The baseline runs the validation split against the skill as it
              arrived — there is no edit yet. The generic hint says "with the
              edited skill", which on step 0 is a description of something that
              does not exist. */}
          {activity.stepNo === 0
            ? "the validation questions, with the skill as it arrived"
            : STEP_PHASES[index]?.hint || "working"}
        </strong>
        {counted && (
          <>
            {" — "}
            <span className="opt-phase-count">
              {activity.done} of {activity.total} answered
              {activity.total > activity.done && `, ${activity.total - activity.done} to go`}
            </span>
          </>
        )}
        {activity.note && <span className="muted"> · {activity.note}</span>}
      </p>
    </div>
  );
}

// What a finished run amounts to, in one sentence. A run whose every candidate
// was rejected ends with the skill it started with, and nothing on the old
// header said so — the reader had to notice that `best: step 0` meant "nothing
// I did helped".
function finishedSentence(run, steps) {
  if (run.status === "interrupted") return "Stopped mid-loop by a restart. Every finished step is kept.";
  if (run.status === "failed") return "This run stopped early.";
  const scored = steps.filter((s) => s.status === "done").length;
  // "Finished 10 steps" was true and said nothing about the afternoon it took.
  // The span is the same one the Started fact carries, through the same
  // function, so the two cannot disagree on one screen.
  const span = formatSpan(runDuration(run, steps).ms);
  const finished = span
    ? `Finished ${plural(scored, "step")} in ${span}.`
    : `Finished ${plural(scored, "step")}.`;
  if (run.best_step == null) return `${finished} No step was scored.`;
  if (run.best_step === 0) {
    return `${finished} No candidate beat the skill it started with — the best score is still the baseline's.`;
  }
  return `${finished} Step ${run.best_step} scored best on validation.`;
}

