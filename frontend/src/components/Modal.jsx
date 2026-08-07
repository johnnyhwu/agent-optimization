import React, { useEffect, useId, useRef, useState } from "react";
import Button, { IconButton } from "./ui/Button.jsx";
import { IconAlert, IconX } from "./icons.jsx";

// Reusable animated modal: backdrop fade + dialog pop-in (see styles.css).
//
// Two things here exist because a dialog that closes by accident destroys
// whatever was typed into it, and several of these dialogs are the only place
// certain text gets written.
//
// **1. A drag out of the dialog must not dismiss it.** Selecting text by
// dragging from inside a field and overshooting the dialog's edge used to close
// it. `onClick` on the overlay looks like it is guarded by the dialog's own
// `stopPropagation`, and for a plain click it is — but a `click` event is
// dispatched to the nearest **common ancestor** of the mousedown and mouseup
// targets. Press inside the dialog, release on the overlay, and that ancestor is
// the overlay itself: its handler runs and the dialog's never does, because the
// event did not travel up through it. So the press position is recorded and both
// ends of the gesture have to have happened on the backdrop.
//
// **2. Dismissing unsaved work asks first.** Tracked by listening for native
// `input`/`change` events on the dialog body rather than by a prop each dialog
// passes: nine call sites would rot, and — the load-bearing part — a native
// `input` event only fires for real user edits. Programmatic React updates do
// not produce one, so RunConfigDialog filling itself in from
// `GET /run-config/defaults` after mount cannot register as "the user typed
// something".
//
// ESC and backdrop click both go through that guard. The close button does not:
// pressing the X is unambiguous.
//
// A dialog with a mode of its own (the upload preview's expanded editor) passes
// onDismiss to step out of that mode instead — otherwise ESC would throw away
// in-progress edits.
export default function Modal({
  title, subtitle, onClose, children, footer, width = 560, height, onDismiss,
  dirty: dirtyProp,
}) {
  const dismiss = onDismiss || onClose;
  const titleId = useId();
  const bodyRef = useRef(null);
  // Where the current press started. Read on click, which fires after mouseup.
  const pressedBackdrop = useRef(false);
  const [edited, setEdited] = useState(false);
  const [confirming, setConfirming] = useState(false);

  // An explicit prop wins, for a caller that knows something the DOM doesn't.
  const dirty = dirtyProp === undefined ? edited : dirtyProp;

  // Ask before throwing work away; otherwise go.
  function tryDismiss() {
    if (dirty) setConfirming(true);
    else dismiss();
  }

  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== "Escape") return;
      // While the confirm is up, Escape backs out of the confirm rather than
      // out of the dialog — the same key should not both ask the question and
      // answer it destructively.
      if (confirming) {
        setConfirming(false);
        return;
      }
      if (dirty) setConfirming(true);
      else dismiss();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [dismiss, dirty, confirming]);

  // Land in the dialog rather than behind it. Without this the first Tab after
  // opening one walked the page underneath, which on a dialog whose whole
  // purpose is a form meant reaching for the mouse every time.
  useEffect(() => {
    const first = bodyRef.current?.querySelector(
      "input:not([type=hidden]):not([disabled]), textarea:not([disabled]), select:not([disabled]), button:not([disabled])"
    );
    first?.focus();
  }, []);

  // Any real edit anywhere in the body arms the guard. Capture phase so it still
  // sees events from children that stop propagation.
  useEffect(() => {
    const node = bodyRef.current;
    if (!node) return undefined;
    const mark = () => setEdited(true);
    node.addEventListener("input", mark, true);
    node.addEventListener("change", mark, true);
    return () => {
      node.removeEventListener("input", mark, true);
      node.removeEventListener("change", mark, true);
    };
  }, []);

  return (
    <div
      className="overlay"
      onMouseDown={(e) => {
        pressedBackdrop.current = e.target === e.currentTarget;
      }}
      onClick={(e) => {
        // Both ends of the gesture on the backdrop, or it wasn't a backdrop click.
        if (e.target === e.currentTarget && pressedBackdrop.current) tryDismiss();
        pressedBackdrop.current = false;
      }}
    >
      <div
        className="dialog"
        style={{ width, height }}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
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

        {/* Inside the dialog rather than a second Modal on top of it: stacking
            two overlays to ask one question is more chrome than the question is
            worth, and the thing at risk is right underneath. */}
        {confirming && (
          <div className="dialog-confirm">
            <div className="dialog-confirm-box">
              <span className="dialog-confirm-mark"><IconAlert size={18} /></span>
              <div className="dialog-confirm-text">
                <strong>Discard your changes?</strong>
                <p>What you have entered here has not been saved.</p>
              </div>
              <div className="dialog-confirm-actions">
                <Button variant="secondary" onClick={() => setConfirming(false)} autoFocus>
                  Keep editing
                </Button>
                <Button variant="danger" onClick={dismiss}>Discard</Button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
