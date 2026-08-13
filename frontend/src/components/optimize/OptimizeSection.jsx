import React from "react";
import { href, navigate } from "../../useHashRoute.js";
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

  return (
    <div className="opt-section">
      <RunList
        subject={subject}
        activeId={route.tier === "run" ? route.runId : null}
        onOpen={(run) => navigate(href.optimizeRun(run.id))}
        onNew={() => navigate(href.optimizeNew())}
      />
      <div className="opt-pane">
        {route.tier === "run" ? (
          <RunPanel key={route.runId} runId={route.runId} subject={subject} />
        ) : (
          <RunList.Intro onNew={() => navigate(href.optimizeNew())} />
        )}
      </div>
    </div>
  );
}
