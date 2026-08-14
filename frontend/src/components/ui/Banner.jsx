import React from "react";
import { IconAlert, IconCheck, IconClock, IconInfo, IconX } from "../icons.jsx";

// An inline message about the thing next to it — a failed trace, a diagnosis
// that couldn't be generated, a caveat on one that could.
//
// There were five hand-rolled variants (`.banner`, `.error-banner`, `.caveat`,
// `.generating`, `.banner-explain`), and each prefixed its own text with a
// literal glyph: "✕ Could not load the trace", "⚠ Caveat", "⏳ Trace is
// generating". Those glyphs are the most visible tell in the whole interface that
// nobody drew this on purpose — they render in the system emoji font, at whatever
// size and baseline that font decides, next to SVG icons that were drawn to sit
// on the text baseline.
//
// So the mark is part of the component, chosen from `tone`, and callers pass
// words only.
// The tone vocabulary. `src/ui_vocabulary.test.js` checks that every word here
// has a `.ui-banner-*` rule behind it and that no call site invents one — both
// of which had already gone wrong by the time it was written.
const MARK = {
  info: IconInfo,
  error: IconX,
  warning: IconAlert,
  pending: IconClock,
  success: IconCheck,
};

export default function Banner({
  tone = "info",
  title,
  actions,
  icon,
  children,
  className = "",
}) {
  const Mark = MARK[tone] || IconInfo;
  return (
    <div className={`ui-banner ui-banner-${tone} ${className}`.trim()}>
      <span className="ui-banner-mark">{icon || <Mark size={15} />}</span>
      <div className="ui-banner-content">
        {title && <strong className="ui-banner-title">{title}</strong>}
        {children}
        {actions && <div className="ui-banner-actions">{actions}</div>}
      </div>
    </div>
  );
}

// Raw output — a stack trace, an upstream error body. Monospace and boxed, so it
// reads as "this is what the other system said" rather than as our own prose.
export function BannerDetail({ children }) {
  return <div className="ui-banner-detail">{children}</div>;
}
