import React from "react";

// Progress for ONE question. RunProgress and RunStatusBar are aggregate-shaped —
// a 0/1 bar and a stacked bar over a total of one say nothing — but the per-stage
// `phase` the backend already publishes says everything: which of the four calls
// is outstanding right now.
//
// Stages that do not apply are shown struck through rather than hidden, so the
// row does not change shape when an expected answer is added, and so "not judged"
// reads as a choice rather than as something still pending.
const ORDER = ["pending", "answered", "judged", "traced", "diagnosed"];

export default function PhaseSteps({ attempt }) {
  if (!attempt) return null;
  const { phase, status } = attempt;
  const reached = ORDER.indexOf(phase);
  const running = status === "running";

  const steps = [
    { key: "answered", label: "Agent", applies: true },
    { key: "judged", label: "Judge", applies: attempt.has_expected_answer },
    { key: "traced", label: "Trace", applies: true },
    { key: "diagnosed", label: "Diagnosis", applies: attempt.has_expected_reasoning },
  ];

  // The first applicable step that hasn't been reached is the one in flight —
  // but only while the attempt is actually running. On a stopped or failed
  // attempt nothing is in flight, and showing a spinner would be a lie.
  const activeKey = running
    ? steps.find((s) => s.applies && ORDER.indexOf(s.key) > reached)?.key
    : null;

  return (
    <div className="phasesteps" role="status">
      {steps.map((step) => {
        const done = ORDER.indexOf(step.key) <= reached;
        const cls = !step.applies
          ? "skipped"
          : done
            ? "done"
            : step.key === activeKey
              ? "active"
              : "todo";
        return (
          <span key={step.key} className={`phasestep ${cls}`}>
            <span className="dot" />
            {step.label}
          </span>
        );
      })}
      {status === "failed" && <span className="phasestep failed">failed</span>}
      {status === "cancelled" && <span className="phasestep failed">stopped</span>}
    </div>
  );
}
