import React, { useEffect, useId, useRef, useState } from "react";
import { IconButton } from "./Button.jsx";
import { IconX } from "../icons.jsx";

// A right-hand sheet, for editing something alongside the thing it affects.
//
// It exists for the playground's four composer panels, which used to expand
// *inline* above the three columns. That cost the columns up to 400px
// (`.composer-panel { max-height: 400px }`), and for the config tree the cap was
// removed entirely — so opening a panel could push the trace, the span detail
// and the send button off the bottom of the window. A sheet takes width, which
// on a 1440px desktop is the axis with room to spare, instead of height, which
// is the axis the trace needs.
//
// Two behaviours are shared with Modal for the same reason:
//
//   * **Dismissal is gated on where the press started.** A drag out of a
//     textarea that ends on the scrim must not close the sheet — that is the
//     bug that lost typed input in the dialogs.
//   * **Children stay mounted once opened**, hidden with `hidden` + `inert`
//     rather than unmounted. WorkspaceEditor holds half-typed, not-yet-valid
//     JSON in local state; unmounting it on close would throw that away, which
//     is the same data loss wearing different clothes.
export default function Drawer({
  open,
  title,
  subtitle,
  onClose,
  width = 620,
  footer,
  children,
}) {
  const titleId = useId();
  const pressedScrim = useRef(false);
  // Once true, stays true: the point is to keep the contents alive across
  // closes, so the first open is the only one that mounts them.
  const [everOpened, setEverOpened] = useState(open);

  useEffect(() => {
    if (open) setEverOpened(true);
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      if (e.key !== "Escape") return;
      // The sheet is the innermost thing open, so it takes the key before any
      // dialog behind it does.
      e.stopPropagation();
      onClose();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [open, onClose]);

  if (!everOpened) return null;

  return (
    <div
      className={`ui-drawer-root${open ? " is-open" : ""}`}
      // `inert` keeps a closed sheet out of the tab order and away from the
      // screen reader while its DOM — and its state — stays alive.
      {...(open ? {} : { inert: "", "aria-hidden": "true" })}
    >
      <div
        className="ui-drawer-scrim"
        onMouseDown={(e) => {
          pressedScrim.current = e.target === e.currentTarget;
        }}
        onClick={(e) => {
          if (e.target === e.currentTarget && pressedScrim.current) onClose();
          pressedScrim.current = false;
        }}
      />
      <aside
        className="ui-drawer"
        style={{ width }}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <header className="ui-drawer-head">
          <div className="ui-drawer-head-text">
            <h3 id={titleId}>{title}</h3>
            {subtitle && <p className="ui-drawer-sub">{subtitle}</p>}
          </div>
          <IconButton label="Close" icon={<IconX size={16} />} onClick={onClose} />
        </header>
        <div className="ui-drawer-body">{children}</div>
        {footer && <div className="ui-drawer-foot">{footer}</div>}
      </aside>
    </div>
  );
}
