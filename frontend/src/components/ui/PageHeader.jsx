import React from "react";

// The top of a screen — and the place the "one primary action" rule is enforced
// structurally rather than by discipline.
//
// The run history had four buttons here (Download, Edit questions, Set config,
// Run eval), two of them wearing the same gear icon, all four the same size. A
// developer arriving to run an eval had to read four labels to find the one thing
// the screen is for.
//
// So the API only accepts **one** `primary`. Everything else goes in `menu`,
// which renders as an overflow. There is no prop for a second prominent button,
// which means a future screen cannot quietly grow one back.
export default function PageHeader({ title, subtitle, meta, primary, menu, className = "" }) {
  return (
    <header className={`ui-page-head ${className}`.trim()}>
      <div className="ui-page-head-text">
        <h2 className="ui-page-title">{title}</h2>
        {subtitle && <p className="ui-page-sub">{subtitle}</p>}
        {meta && <div className="ui-page-meta">{meta}</div>}
      </div>
      {(primary || menu) && (
        <div className="ui-page-head-actions">
          {menu}
          {primary}
        </div>
      )}
    </header>
  );
}
