import React, { useEffect } from "react";
import { IconX } from "./icons.jsx";

// Reusable animated modal: backdrop fade + dialog pop-in (see styles.css).
// ESC and backdrop click dismiss it. A dialog with a mode of its own (the
// upload preview's expanded editor) passes onDismiss to step out of that mode
// instead — otherwise ESC would throw away in-progress edits.
export default function Modal({
  title, subtitle, onClose, children, footer, width = 560, height, onDismiss,
}) {
  const dismiss = onDismiss || onClose;
  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && dismiss();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [dismiss]);

  return (
    <div className="overlay" onClick={dismiss}>
      <div
        className="dialog"
        style={{ width, height }}
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="dialog-head">
          <div>
            <h3>{title}</h3>
            {subtitle && <p className="dialog-sub">{subtitle}</p>}
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            <IconX />
          </button>
        </div>
        <div className="dialog-body">{children}</div>
        {footer && <div className="dialog-foot">{footer}</div>}
      </div>
    </div>
  );
}
