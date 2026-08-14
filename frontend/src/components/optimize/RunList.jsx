import React, { useEffect, useState } from "react";
import { api } from "../../api.js";
import Badge from "../ui/Badge.jsx";
import Button from "../ui/Button.jsx";
import EmptyState from "../ui/EmptyState.jsx";
import Skeleton from "../ui/Skeleton.jsx";
import { IconPlus, IconSparkles } from "../icons.jsx";
import { stepProgress } from "../../optimize_steps.js";

// The left rail of the Optimize section: every run this person can see, newest
// first. One run is one complete optimization — a dataset, a skill, and the
// steps it took.

export const STATUS_TONE = {
  running: "accent",
  pending: "neutral",
  completed: "success",
  failed: "danger",
  cancelled: "neutral",
  // Not a failure. A restart caught this run mid-loop; its finished steps are
  // on disk and it can be picked back up, which is what the word has to convey.
  interrupted: "warning",
};

export default function RunList({ subject, activeId, onOpen, onNew, revision = 0 }) {
  const [runs, setRuns] = useState(null);
  const [error, setError] = useState(null);

  // `revision` is bumped by whoever knows a run's state changed — the panel
  // beside this, when its stream reports a step or a terminal event. The rail
  // used to fetch once and never again, so starting a run from the wizard and
  // landing on its page left "pending" sitting in the rail for the rest of the
  // hour, next to a panel showing it running.
  useEffect(() => {
    let cancelled = false;
    api
      .listOptimizationRuns({ limit: 50 })
      .then((page) => !cancelled && setRuns(page.items || []))
      .catch((e) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [subject, revision]);

  return (
    <aside className="opt-runlist" aria-label="Optimization runs">
      <div className="opt-runlist-head">
        <h3>Runs</h3>
        <Button variant="primary" icon={<IconPlus size={15} />} onClick={onNew}>
          New
        </Button>
      </div>

      {error && <p className="error">{error}</p>}
      {!runs && !error && <Skeleton variant="row" count={4} />}

      {/* The composed empty state, not a bare sentence. `RunList.Intro` below
          has said the useful thing all along; the rail said "No runs yet." and
          left the New button above as the only clue what to do with that. */}
      {runs && runs.length === 0 && (
        <EmptyState size="sm" icon={<IconSparkles size={18} />} title="No runs yet">
          A run trains one skill against your eval sets.
        </EmptyState>
      )}

      {runs && runs.length > 0 && (
        <ul className="opt-runlist-items">
          {runs.map((run) => (
            <li key={run.id}>
              <button
                type="button"
                className={`opt-runitem${String(run.id) === String(activeId) ? " is-active" : ""}`}
                onClick={() => onOpen(run)}
                aria-current={String(run.id) === String(activeId) ? "true" : undefined}
              >
                <span className="opt-runitem-top">
                  <span className="opt-runitem-name">
                    {run.name || new Date(run.started_at).toLocaleString()}
                  </span>
                  <Badge tone={STATUS_TONE[run.status] || "neutral"} size="sm">
                    {run.status}
                  </Badge>
                </span>
                <span className="opt-runitem-meta">
                  <code>{run.skill_name}</code>
                  <span>{run.mode}</span>
                  {/* Progress as steps rather than a percentage: a run that was
                      cancelled at step 3 of 12 did three steps' worth of real
                      work, and "25%" says the opposite.

                      Through the shared helper, because this rail and the panel
                      beside it used to disagree about the same run: the rail
                      left the baseline out of the denominator and the panel put
                      it in, so one read 4/12 while the other read 5/13. */}
                  <span>{stepProgress(run, null).label} steps</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </aside>
  );
}

RunList.Intro = function Intro({ onNew }) {
  return (
    <EmptyState
      icon={<IconSparkles size={22} />}
      title="Train a skill against your eval sets"
      size="lg"
      action={
        <Button variant="primary" icon={<IconPlus size={15} />} onClick={onNew}>
          New optimization run
        </Button>
      }
    >
      A run answers a batch of your questions with the skill as it stands, reads
      the failures, rewrites the skill, and keeps the rewrite only if it beats
      the old one on questions held back for the purpose. Pick a run on the left
      to see how it went.
    </EmptyState>
  );
};
