import React, { useEffect, useId, useRef, useState } from "react";
import { IconButton } from "./Button.jsx";
import { IconX } from "../icons.jsx";

// A large centered dialog, for editing something that needs room.
//
// It exists for the playground's four composer panels, and it has now been the
// wrong shape twice. First they expanded *inline* above the three columns, which
// cost the columns up to 400px and could push the trace and the send button off
// the bottom of the window. Then they became a right-hand sheet, which fixed the
// height problem and created a width one: 560–720px is not enough for a skill
// file beside its file list, or for two paragraphs of expected answer side by
// side, so the content bunched into the top-left and left the rest blank.
//
// A sheet is the right shape when the page behind it stays usable. This one
// never was — it set `aria-modal` and blurred everything behind it from the
// start. Once the page is blocked anyway, there is no reason to stay pinned to
// one edge, and a centered dialog can be as wide as the content needs.
//
// Two behaviours carry over unchanged, both because losing typed work is the
// failure this component keeps being redesigned around:
//
//   * **Dismissal is gated on where the press started.** A drag out of a
//     textarea that ends on the scrim must not close the dialog.
//   * **Children stay mounted once opened**, hidden with `hidden` + `inert`
//     rather than unmounted. WorkspaceEditor holds half-typed, not-yet-valid
//     JSON in local state; unmounting it on close would throw that away.
//
// That second one is also why this is not `Modal`: Modal unmounts its children
// and guards closing with a "Discard your changes?" prompt, both of which are
// wrong here, where the edits are meant to survive until the question is sent.
export default function PanelDialog({
  open,
  title,
  subtitle,
  onClose,
  width = 880,
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
      // The dialog is the innermost thing open, so it takes the key before any
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
      className={`ui-panel-root${open ? " is-open" : ""}`}
      // `inert` keeps a closed dialog out of the tab order and away from the
      // screen reader while its DOM — and its state — stays alive.
      {...(open ? {} : { inert: "", "aria-hidden": "true" })}
    >
      <div
        className="ui-panel-scrim"
        onMouseDown={(e) => {
          pressedScrim.current = e.target === e.currentTarget;
        }}
        onClick={(e) => {
          if (e.target === e.currentTarget && pressedScrim.current) onClose();
          pressedScrim.current = false;
        }}
      />
      <div
        className="ui-panel"
        style={{ width: `min(${width}px, 92vw)` }}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <header className="ui-panel-head">
          <div className="ui-panel-head-text">
            <h3 id={titleId}>{title}</h3>
            {subtitle && <p className="ui-panel-sub">{subtitle}</p>}
          </div>
          <IconButton label="Close" icon={<IconX size={16} />} onClick={onClose} />
        </header>
        <div className="ui-panel-body">{children}</div>
        {footer && <div className="ui-panel-foot">{footer}</div>}
      </div>
    </div>
  );
}
