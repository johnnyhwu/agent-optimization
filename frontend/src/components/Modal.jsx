import React, { useEffect, useId, useRef } from "react";
import { IconButton } from "./ui/Button.jsx";
import { IconX } from "./icons.jsx";

// Reusable animated modal: backdrop fade + dialog pop-in (see styles.css).
// ESC and backdrop click dismiss it. A dialog with a mode of its own (the
// upload preview's expanded editor) passes onDismiss to step out of that mode
// instead — otherwise ESC would throw away in-progress edits.
export default function Modal({
  title, subtitle, onClose, children, footer, width = 560, height, onDismiss,
}) {
  const dismiss = onDismiss || onClose;
  const titleId = useId();
  const bodyRef = useRef(null);
  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && dismiss();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [dismiss]);

  // Land in the dialog rather than behind it. Without this the first Tab after
  // opening one walked the page underneath, which on a dialog whose whole
  // purpose is a form meant reaching for the mouse every time.
  useEffect(() => {
    const first = bodyRef.current?.querySelector(
      "input:not([type=hidden]):not([disabled]), textarea:not([disabled]), select:not([disabled]), button:not([disabled])"
    );
    first?.focus();
  }, []);

  return (
    <div className="overlay" onClick={dismiss}>
      <div
        className="dialog"
        style={{ width, height }}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="dialog-head">
          <div>
            <h3 id={titleId}>{title}</h3>
            {subtitle && <p className="dialog-sub">{subtitle}</p>}
          </div>
          <IconButton label="Close" icon={<IconX size={16} />} onClick={onClose} />
        </div>
        <div className="dialog-body" ref={bodyRef}>{children}</div>
        {footer && <div className="dialog-foot">{footer}</div>}
      </div>
    </div>
  );
}
