import React from "react";
import { createRoot } from "react-dom/client";
import Wizard from "../src/components/optimize/Wizard.jsx";
import RunDuration from "../src/components/optimize/RunDuration.jsx";
import Fact from "../src/components/optimize/Fact.jsx";
import { ToastProvider } from "../src/components/Toast.jsx";
import "@fontsource-variable/inter";
import "@fontsource-variable/space-grotesk";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/600.css";
import "../src/styles.css";
import "../src/ui.css";

document.documentElement.setAttribute("data-theme", "light");

// A run that started a controllable number of seconds ago, so the ticking
// header can be photographed at chosen widths.
const startedAgo = (s) => new Date(Date.now() - s * 1000).toISOString();

function DurationStrip() {
  // The real facts row from RunHeader, at the width it gets on a normal window.
  const q = new URLSearchParams(location.search);
  const secs = Number(q.get("secs") || 9);
  const w = Number(q.get("w") || 900);
  const run = { status: "running", started_at: startedAgo(secs), completed_at: null,
                num_epochs: 3, steps_per_epoch: 4, batch_size: 6, created_by: "alice" };
  return (
    <div style={{ padding: 16, width: w }}>
      <dl className="opt-runfacts">
        <Fact label="Mode" value="isolated" sub="edits the skill body" />
        <Fact label="Skill" value="invoice-reading" sub="1 skill" />
        <Fact label="Schedule" value="3 x 4" sub="3 epochs of 4 steps" />
        <Fact label="Batch" value={6} sub="questions per step" />
        <Fact label="Started" value="2 Sep 2026, 10:00"
              sub={<RunDuration run={run} steps={[]} by="alice" />} />
      </dl>
      <p id="probe" style={{ marginTop: 12 }}>content under the facts row</p>
    </div>
  );
}

function Harness() {
  const mode = new URLSearchParams(location.search).get("view");
  if (mode === "duration") return <DurationStrip />;
  return (
    <div className="app" style={{ gridTemplateColumns: "1fr" }}>
      <div className="main">
        <div className="page">
          <Wizard />
        </div>
      </div>
    </div>
  );
}

createRoot(document.getElementById("root")).render(
  <ToastProvider>
    <Harness />
  </ToastProvider>,
);
