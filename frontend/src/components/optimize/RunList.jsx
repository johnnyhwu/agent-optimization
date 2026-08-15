import React, { useEffect, useRef, useState } from "react";
import { api } from "../../api.js";
import Badge from "../ui/Badge.jsx";
import Button from "../ui/Button.jsx";
import EmptyState from "../ui/EmptyState.jsx";
import Skeleton from "../ui/Skeleton.jsx";
import { IconPlus, IconSparkles } from "../icons.jsx";
import { stepProgress } from "../../optimize_steps.js";
import { runStartedAt, runTitle } from "../../optimize_run_label.js";

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

export default function RunList({ subject, activeId, onOpen, onNew, onLoaded, revision = 0 }) {
  const [runs, setRuns] = useState(null);
  const [error, setError] = useState(null);
  // Held in a ref so the fetch effect does not re-run when the parent hands it a
  // fresh closure, which is every render.
  const loaded = useRef(onLoaded);
  loaded.current = onLoaded;

  // `revision` is bumped by whoever knows a run's state changed — the panel
  // beside this, when its stream reports a step or a terminal event. The rail
  // used to fetch once and never again, so starting a run from the wizard and
  // landing on its page left "pending" sitting in the rail for the rest of the
  // hour, next to a panel showing it running.
  useEffect(() => {
    let cancelled = false;
    api
      .listOptimizationRuns({ limit: 50 })
      .then((page) => {
        if (cancelled) return;
        const items = page.items || [];
        setRuns(items);
        loaded.current?.(items);
      })
      .catch((e) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [subject, revision]);

  return (
    <aside className="opt-runlist" aria-label="Optimization runs">
      {/* The rail goes quiet when it has nothing to list. With no runs, the
          pane beside it is `RunList.Intro` — which says the same thing at
          length and offers the same button — and the two together put two
          sparkle icons, two headings and two New buttons on one empty screen. */}
      {runs && runs.length > 0 && (
        <div className="opt-runlist-head">
          <h3>Runs</h3>
          {/* Small and secondary. A full-height filled button in a 260px rail,
              beside a 13px heading, was the heaviest thing on a column whose job
              is to be a list — it read as the point of the pane rather than as
              the way out of it. The primary New is on the empty state, which is
              the screen where starting a run *is* the point. */}
          <Button
            variant="secondary"
            size="sm"
            icon={<IconPlus size={14} />}
            onClick={onNew}
          >
            New
          </Button>
        </div>
      )}

      {error && <p className="error">{error}</p>}
      {!runs && !error && <Skeleton variant="row" count={4} />}

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
                {/* Through the shared helper, because this rail and the panel
                    beside it used to fall back differently for a run nobody
                    named — which is most of them, the wizard's Name field
                    offering its suggestion as a placeholder. The rail showed a
                    locale timestamp and the panel showed "Optimizing billing",
                    so one run appeared under two names on one screen. */}
                <span className="opt-runitem-top">
                  <span className="opt-runitem-name">{runTitle(run)}</span>
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
                {/* The timestamp the name used to be. It is still worth having —
                    it is how two runs of the same skill are told apart — but as
                    the row's identity it was competing with the name, and in
                    locale form it was the widest thing in a 260px rail. */}
                <span className="opt-runitem-when">{runStartedAt(run)}</span>
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
      the old one on questions held back for the purpose.
    </EmptyState>
  );
};
