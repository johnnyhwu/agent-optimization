import React, { useEffect, useState } from "react";
import { api } from "../../api.js";
import Badge from "../ui/Badge.jsx";
import Banner from "../ui/Banner.jsx";
import Button from "../ui/Button.jsx";
import Card, { CardHeader } from "../ui/Card.jsx";
import Skeleton from "../ui/Skeleton.jsx";
import { IconDownload, IconPlay, IconRefresh, IconStop } from "../icons.jsx";
import { useToast } from "../Toast.jsx";
import { href, navigate } from "../../useHashRoute.js";
import { runWarnings } from "../../optimize_warnings.js";
import {
  applyEvent,
  emptySteps,
  replaceSteps,
  stepList,
  stepProgress,
} from "../../optimize_steps.js";
import { STATUS_TONE } from "./RunList.jsx";
import ProgressChart from "./ProgressChart.jsx";
import StepCard from "./StepCard.jsx";

// One run's overview. This is the header and the live state; the chart, the
// step table and the two detail views land on top of it in the next phases.
//
// The stream is the point. A run is an hour long, and a page that only showed
// what was true when it loaded would be wrong within a minute — so the snapshot
// arrives on the stream too, which also means a developer opening the page
// halfway through gets the steps that already happened rather than a blank
// screen until the next one lands.

export default function RunPanel({ runId, subject }) {
  const toast = useToast();
  const [run, setRun] = useState(null);
  const [error, setError] = useState(null);
  const [live, setLive] = useState(emptySteps);
  const [busy, setBusy] = useState(false);
  const [pinned, setPinned] = useState(null);
  const [metric, setMetric] = useState("hard");
  // Which download is in flight — "best", a step number, or null. One boolean
  // put both this header's button and the pinned card's into a spinner
  // whichever was pressed, so the page reported work it was not doing.
  const [downloading, setDownloading] = useState(null);

  // The run row *and* its steps: `getOptimizationRun` carries both, and the
  // steps it carries are authoritative — that is what makes this the recovery
  // for every case the stream cannot patch its way out of.
  async function reload() {
    try {
      const fresh = await api.getOptimizationRun(runId);
      setRun(fresh);
      setLive((l) => replaceSteps(l, fresh.steps || []));
    } catch (e) {
      setError(e.message);
    }
  }

  useEffect(() => {
    reload();
  }, [runId]);

  // A pin outlives the step it points at if a refetch no longer carries it — a
  // resumed run reopening an aborted step, say. The card then simply stopped
  // rendering, leaving a pin nothing on the page could explain or undo.
  useEffect(() => {
    if (pinned != null && !live.byNo.has(pinned)) setPinned(null);
  }, [pinned, live]);

  useEffect(() => {
    const stream = api.openOptimizationProgress(runId);

    // A stream handler is handed the SSE frame, whose `data` is the raw JSON
    // text — every other stream in this app parses it, and this one did not. It
    // read `e.steps` and `e.step_no` straight off the frame, where both are
    // undefined, which is why the snapshot never landed a single step and why
    // the caption above the chart read "step undefined · undefined" for the
    // entire run. Parsing in one place so there is no second call site to
    // forget; a malformed frame is dropped rather than thrown, because throwing
    // here would take the read loop down with it.
    const parse = (fn) => (e) => {
      let payload;
      try {
        payload = JSON.parse(e.data);
      } catch {
        return;
      }
      fn(payload);
    };

    // The snapshot is the stream's own opening statement of the same thing a
    // refetch gives, so it replaces rather than merges.
    const onSnapshot = parse((d) => setLive((l) => replaceSteps(l, d.steps || [])));
    // Everything else is a slice of a step row, and the reducer knows which.
    // These were the events the page was already being sent and throwing away:
    // a step is assembled from `step_started`, two `rollout_done`s minutes
    // apart, `update_done` and `gate_done`, and subscribing to only the first
    // and last of those is why the chart never moved.
    const events = [
      "step_started",
      "rollout_done",
      "rollout_retry",
      "reflect_done",
      "update_done",
      "gate_done",
      "slow_update_done",
    ];
    for (const name of events) {
      stream.addEventListener(name, parse((d) => setLive((l) => applyEvent(l, name, d))));
    }

    // A terminal event has to refetch rather than patch: the run row carries the
    // final status, the best step and the error message, and none of those are
    // reconstructable from the events alone.
    const onDone = parse((d) => {
      setLive((l) => applyEvent(l, "run_completed", d));
      reload();
    });
    stream.addEventListener("snapshot", onSnapshot);
    stream.addEventListener("run_completed", onDone);
    // The hub drops the oldest event rather than growing a queue behind a
    // subscriber that stopped reading, and says so. Refetching is the recovery.
    stream.addEventListener("resync", reload);
    return () => stream.close();
  }, [runId]);

  async function act(fn, message) {
    setBusy(true);
    try {
      await fn(runId);
      toast.success(message);
      await reload();
    } catch (e) {
      toast.error(e.message);
    } finally {
      setBusy(false);
    }
  }

  // `step` is "best" or a step number. The manifest inside the zip says which
  // one it turned out to be, so a download is never anonymous once it is on
  // disk — but the toast should say so too, while the page is still open.
  async function downloadSkill(step) {
    setDownloading(step);
    try {
      const filename = await api.downloadOptimizedSkill(runId, step);
      toast.success(`Saved ${filename}`);
    } catch (e) {
      toast.error(e.message);
    } finally {
      setDownloading(null);
    }
  }

  if (error) return <Banner tone="error" title="Could not load this run">{error}</Banner>;
  if (!run) return <Skeleton variant="row" count={5} />;

  const isMine = run.created_by === subject;
  const steps = stepList(live);
  const progress = stepProgress(run, steps);
  const pinnedStep = pinned == null ? null : steps.find((s) => s.step_no === pinned);

  return (
    <div className="opt-run">
      <Card>
        <CardHeader
          title={run.name || `Optimizing ${run.skill_name}`}
          actions={
            <>
              {/* The run's only output. Offered whenever there is a scored step,
                  including on a cancelled or interrupted run — the steps that
                  finished are real, and their skill is the reason to have run
                  it at all. */}
              {run.best_step != null && (
                <Button
                  variant="secondary"
                  icon={<IconDownload size={15} />}
                  loading={downloading === "best"}
                  onClick={() => downloadSkill("best")}
                  title={`Step ${run.best_step}, the best this run scored on validation`}
                >
                  Download best skill
                </Button>
              )}
              {run.status === "running" && isMine && (
                <Button variant="danger" icon={<IconStop size={15} />} loading={busy}
                        onClick={() => act(api.cancelOptimizationRun, "Stopping — finished steps are kept.")}>
                  Stop
                </Button>
              )}
              {/* Resume is offered only for `interrupted`: a cancelled run was a
                  decision and a failed one stopped because continuing would
                  produce a misleading result. Both stay available as the
                  starting point for a new run instead. */}
              {run.status === "interrupted" && isMine && (
                <Button variant="primary" icon={<IconPlay size={15} />} loading={busy}
                        onClick={() => act(api.resumeOptimizationRun, "Resuming from the last completed step.")}>
                  Resume
                </Button>
              )}
              <Button variant="ghost" icon={<IconRefresh size={15} />} onClick={reload}>
                Refresh
              </Button>
            </>
          }
        />
        <div className="opt-run-meta">
          <Badge tone={STATUS_TONE[run.status] || "neutral"}>{run.status}</Badge>
          <span><code>{run.skill_name}</code></span>
          <span>{run.mode}</span>
          <span>{run.n_train} train · {run.n_val} validation</span>
          <span>{progress.label} steps</span>
          {/* Both halves guarded: a run can carry a best step before its score
              has been written back, and `(null * 100).toFixed(0)` is the string
              "NaN" sitting in the middle of the run header. */}
          {run.best_step != null && run.best_score != null && (
            <Badge tone="success" mono>
              best: step {run.best_step} · {(run.best_score * 100).toFixed(0)}%
            </Badge>
          )}
          {live.phase && <span className="opt-run-phase">{live.phase}</span>}
        </div>

        {run.status === "interrupted" && (
          <Banner tone="warning" title="This run was interrupted">
            The backend restarted while it was running. Every completed step is on
            disk — resuming continues from the one after the last that finished
            rather than starting over.
          </Banner>
        )}
        {run.error_message && run.status === "failed" && (
          <Banner tone="error" title="This run stopped early">
            {run.error_message}
          </Banner>
        )}
        {/* One list, computed in one place. These used to be two hand-written
            banners here; the rest of the rules (activation, skill size,
            memorised answers) need the steps as well as the run, and rules
            spread across a render function cannot be tested at all. */}
        {runWarnings(run, steps).map((warning) => (
          <Banner key={warning.id} tone={warning.tone} title={warning.title}>
            {warning.body}
          </Banner>
        ))}
      </Card>

      <Card>
        <CardHeader
          title="Accuracy by step"
          actions={
            <div className="opt-metric-toggle" role="group" aria-label="Scoring metric">
              {["hard", "soft"].map((name) => (
                <Button
                  key={name}
                  variant={metric === name ? "secondary" : "ghost"}
                  onClick={() => setMetric(name)}
                  title={
                    name === "hard"
                      ? "Strictly correct answers only"
                      : "Partial credit, as the judge scored each answer 0–1"
                  }
                >
                  {name}
                </Button>
              ))}
            </div>
          }
        />
        <ProgressChart
          steps={steps}
          totalSteps={run.total_steps}
          bestStep={run.best_step}
          metric={metric}
          pinned={pinned}
          onPick={(stepNo) => setPinned((current) => (current === stepNo ? null : stepNo))}
        />
        {pinnedStep && (
          <StepCard
            step={pinnedStep}
            run={run}
            downloading={downloading === pinnedStep.step_no}
            onClose={() => setPinned(null)}
            onDownload={downloadSkill}
            onOpenRollout={(stepNo, split) =>
              navigate(href.optimizeRollout(runId, stepNo, split))
            }
            onOpenSkill={(stepNo) => navigate(href.optimizeSkill(runId, stepNo))}
          />
        )}
      </Card>

      <Card>
        <CardHeader title="Steps" count={steps.length} />
        <StepTable steps={steps} pinned={pinned} onPick={setPinned} />
      </Card>
    </div>
  );
}

// The chart's accessible equivalent, carrying the same numbers in a form that
// cannot distort them — and the keyboard's way to pin a step, since clicking a
// point on an SVG is a pointer-only gesture.
function StepTable({ steps, pinned, onPick }) {
  if (!steps.length) return <p className="opt-hint">No steps yet.</p>;
  return (
    <table className="opt-steptable">
      <thead>
        <tr>
          <th className="num">Step</th>
          <th className="num">Train</th>
          <th className="num">Validation</th>
          <th>Gate</th>
          <th className="num">Edits</th>
          <th>What changed</th>
        </tr>
      </thead>
      <tbody>
        {steps.map((s) => (
          <tr
            key={s.step_no}
            className={pinned === s.step_no ? "pinned" : undefined}
            tabIndex={0}
            role="button"
            aria-pressed={pinned === s.step_no}
            onClick={() => onPick(pinned === s.step_no ? null : s.step_no)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onPick(pinned === s.step_no ? null : s.step_no);
              }
            }}
          >
            <td className="num">{s.step_no === 0 ? "baseline" : s.step_no}</td>
            <td className="num">{pct(s.train_hard)}</td>
            <td className="num">{pct(s.val_hard)}</td>
            <td>
              {s.gate_action ? (
                <Badge tone={s.gate_action === "reject" ? "neutral" : "success"} size="sm">
                  {s.gate_action === "reject"
                    ? `rejected (${s.gate_reject_reason})`
                    : s.gate_action.replace(/_/g, " ")}
                </Badge>
              ) : (
                "—"
              )}
            </td>
            <td className="num">
              {s.lines_added != null ? `+${s.lines_added} / −${s.lines_removed}` : "—"}
            </td>
            <td className="opt-qtext" title={s.edit_summary || ""}>{s.edit_summary || "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function pct(value) {
  return value == null ? "—" : `${Math.round(value * 100)}%`;
}
