import React, { useCallback, useEffect, useRef, useState } from "react";
import { IconButton } from "./Button.jsx";
import { IconMore } from "../icons.jsx";

// The overflow menu — the piece this app was missing, and the reason its page
// headers grew into rows of four identical grey buttons. With nowhere to put a
// secondary action, every action had to be a top-level one.
//
// The dismissal behaviour is lifted from RunPicker's popover rather than invented
// a second time, including the part that is easy to get wrong: when Escape closes
// this menu it must **stop propagating**, or the same keypress also closes the
// dialog the menu was opened inside, throwing away whatever was typed into it.
export function usePopover() {
  const [open, setOpen] = useState(false);
  const boxRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => {
      if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  return { open, setOpen, boxRef };
}

export default function Menu({
  trigger,
  label = "More actions",
  align = "end",
  width,
  children,
}) {
  const { open, setOpen, boxRef } = usePopover();
  const listRef = useRef(null);

  // Move over whatever items are actually rendered, read from the DOM rather than
  // from the children array: items are frequently conditional (owner-only, or
  // only while a run is in flight), so an index into `children` and an index into
  // what is on screen are not the same number.
  const move = useCallback((delta) => {
    const items = listRef.current?.querySelectorAll('[role="menuitem"]:not([disabled])');
    if (!items?.length) return;
    const list = Array.from(items);
    const at = list.indexOf(document.activeElement);
    const next = at < 0 ? (delta > 0 ? 0 : list.length - 1) : (at + delta + list.length) % list.length;
    list[next].focus();
  }, []);

  function onKeyDown(e) {
    if (e.key === "Escape") {
      if (!open) return;
      e.stopPropagation();
      setOpen(false);
      return;
    }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!open) {
        setOpen(true);
        return;
      }
      move(e.key === "ArrowDown" ? 1 : -1);
    }
  }

  // Focus the first item when opened from the keyboard. Doing it unconditionally
  // would yank focus on a mouse click too, which leaves a focus ring sitting on a
  // menu the user is about to click anyway.
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") move(e.key === "ArrowDown" ? 1 : -1);
    };
    const node = listRef.current;
    node?.addEventListener("keydown", onKey);
    return () => node?.removeEventListener("keydown", onKey);
  }, [open, move]);

  return (
    <div className="ui-menu" ref={boxRef} onKeyDown={onKeyDown}>
      {trigger ? (
        React.cloneElement(trigger, {
          "aria-haspopup": "menu",
          "aria-expanded": open,
          onClick: (e) => {
            trigger.props.onClick?.(e);
            setOpen((o) => !o);
          },
        })
      ) : (
        <IconButton
          label={label}
          icon={<IconMore size={16} />}
          aria-haspopup="menu"
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
        />
      )}

      {open && (
        <div
          className={`ui-menu-pop ui-menu-${align}`}
          role="menu"
          ref={listRef}
          style={width ? { width } : undefined}
          // A click on any item closes the menu. Handled here rather than in each
          // item so a caller can never forget it and leave the menu hanging open
          // over the dialog its item just launched.
          onClick={() => setOpen(false)}
        >
          {children}
        </div>
      )}
    </div>
  );
}

// `variant="danger"` is red at rest, not only on hover: inside a menu there is no
// row of icons for a red one to disturb, and a destructive item that looks exactly
// like its neighbours until the pointer is already on it is the wrong trade.
export function MenuItem({ icon, variant, disabled, className = "", children, onClick, ...rest }) {
  return (
    <button
      type="button"
      role="menuitem"
      className={`ui-menu-item${variant ? ` ui-menu-item-${variant}` : ""} ${className}`.trim()}
      disabled={disabled}
      onClick={onClick}
      {...rest}
    >
      {icon && <span className="ui-menu-icon">{icon}</span>}
      <span className="ui-menu-label">{children}</span>
    </button>
  );
}

export function MenuSeparator() {
  return <div className="ui-menu-sep" role="separator" />;
}
