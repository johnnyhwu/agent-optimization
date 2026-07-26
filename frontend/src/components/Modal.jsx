import React, { useEffect } from "react";
import { IconX } from "./icons.jsx";

// Reusable animated modal: backdrop fade + dialog pop-in (see styles.css).
// ESC and backdrop click close it.
export default function Modal({ title, subtitle, onClose, children, footer, width = 560 }) {
  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="overlay" onClick={onClose}>
      <div
        className="dialog"
        style={{ width }}
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
