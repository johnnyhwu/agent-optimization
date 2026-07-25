import React from "react";

// §6.13 breadcrumb + one-click back to run / back to set.
export default function Breadcrumb({ view, setView }) {
  const crumbs = [{ label: "Eval Sets", target: { tier: "sets" } }];
  if (view.tier === "runs" || view.tier === "detail") {
    crumbs.push({ label: view.es.name, target: { tier: "runs", es: view.es } });
  }
  if (view.tier === "detail") {
    crumbs.push({ label: `${view.runIds.length} run(s) · ${view.mode}`, target: null });
  }
  return (
    <div className="breadcrumb">
      {crumbs.map((c, i) => (
        <React.Fragment key={i}>
          {i > 0 && <span className="sep">/</span>}
          {c.target ? <a onClick={() => setView(c.target)}>{c.label}</a> : <span>{c.label}</span>}
        </React.Fragment>
      ))}
    </div>
  );
}
