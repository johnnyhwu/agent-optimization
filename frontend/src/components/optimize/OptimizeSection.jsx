import React from "react";
import { href, navigate } from "../../useHashRoute.js";
import RunList from "./RunList.jsx";
import RunPanel from "./RunPanel.jsx";
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
