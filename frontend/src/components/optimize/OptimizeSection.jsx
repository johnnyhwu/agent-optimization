import React, { useState } from "react";
import { href, navigate, replace } from "../../useHashRoute.js";
import RolloutDetail from "./RolloutDetail.jsx";
import RunList from "./RunList.jsx";
import RunPanel from "./RunPanel.jsx";
import SkillDiff from "./SkillDiff.jsx";
import Wizard from "./Wizard.jsx";

// The Optimize section: a list of past runs on the left, and whatever the route
// names on the right.
//
// The wizard takes the whole width instead of sitting in the right pane. It is a
// six-step form over lists of sixty questions, and squeezing it beside a rail of
// run history would leave both cramped — the list is for returning to a run, and
// while a new one is being configured there is nothing to return to.

export default function OptimizeSection({ route, subject }) {
  // The rail has no stream of its own — one per visible run would be a
  // connection each — so the open run's panel tells it when something moved.
  // Held here because the rail and the panel are siblings.
  const [railRevision, setRailRevision] = useState(0);
  // null until the rail has loaded. The grid keeps both columns while it is
  // unknown, because that is what the rail's own skeleton is occupying.
  const [hasRuns, setHasRuns] = useState(null);

  if (route.tier === "new") return <Wizard />;
  // Part 1 takes the whole width too. It is two columns of its own — the
  // grouped question list and the analyst pane, which itself opens into the
  // two-column span viewer — and none of that survives being folded into the
  // right half of a page that already has a run rail down its left.
  if (route.tier === "rollout") {
    return (
      <RolloutDetail
        key={`${route.runId}/${route.stepNo}/${route.split}`}
        runId={route.runId}
        stepNo={route.stepNo}
        split={route.split}
        onBack={() => navigate(href.optimizeRun(route.runId))}
        // `replace`, not `navigate`: the two splits are one page's two tabs, and
        // filling the history with every flip between them would make Back mean
        // "the other tab" for as many presses as the reader had compared.
        onPickSplit={(next) =>
          replace(href.optimizeRollout(route.runId, route.stepNo, next))
        }
        onOpenSkill={() => navigate(href.optimizeSkill(route.runId, route.stepNo))}
      />
    );
  }
  // Part 2, likewise full width: a file tree beside a side-by-side diff is
  // three columns of text, and a diff squeezed into a third of the page wraps
  // every line it is supposed to be lining up.
  if (route.tier === "skill") {
    return (
      <SkillDiff
        key={`${route.runId}/${route.stepNo}`}
        runId={route.runId}
        stepNo={route.stepNo}
        onBack={() => navigate(href.optimizeRun(route.runId))}
      />
    );
  }

  // Landing on /optimize with runs to show opens the newest one, rather than an
  // introduction beside a list of the things it is introducing. `replace`, not
  // `navigate`: this is the address the section actually has, so leaving
  // /optimize in the history would make Back bounce between it and the run it
  // immediately redirects to.
  //
  // The rail reports the list it loaded, because it is the thing that loads it —
  // a second fetch here would be the same request twice, and the two could
  // disagree about which run is newest for as long as one was in flight.
  function onRunsLoaded(runs) {
    setHasRuns(runs.length > 0);
    // `runs` is the tier of the bare #/optimize address — the list with nothing
    // opened. Not "run", which already names one.
    if (route.tier !== "runs" || !runs.length) return;
    replace(href.optimizeRun(runs[0].id));
  }

  return (
    // With no runs the rail renders nothing, and a two-column grid would leave
    // its 260px standing empty — pushing the one thing on the screen off centre
    // to make room for a list that does not exist.
    <div className={`opt-section${hasRuns === false ? " is-bare" : ""}`}>
      <RunList
        subject={subject}
        revision={railRevision}
        activeId={route.tier === "run" ? route.runId : null}
        onOpen={(run) => navigate(href.optimizeRun(run.id))}
        onNew={() => navigate(href.optimizeNew())}
        onLoaded={onRunsLoaded}
      />
      <div className="opt-pane">
        {route.tier === "run" ? (
          <RunPanel
            key={route.runId}
            runId={route.runId}
            subject={subject}
            onRunChanged={() => setRailRevision((n) => n + 1)}
          />
        ) : (
          // Only ever reached with no runs to open — see `onRunsLoaded`. Its
          // New button is therefore the only one on the screen: the rail keeps
          // its own for when there is a list to sit above, and the two used to
          // be shown together beside two stacked empty states.
          <RunList.Intro onNew={() => navigate(href.optimizeNew())} />
        )}
      </div>
    </div>
  );
}
