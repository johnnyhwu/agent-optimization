import React from "react";

// The one button in this app.
//
// Before this component there were six ways to write one: `className="primary"`,
// `"ghost"`, `"danger"`, `"icon-btn"`, `button.linkish` and `.link-btn` — the last
// two being the same idea with different padding. A screen could therefore end up
// with four controls of identical visual weight and no way to tell which one the
// developer was meant to press.
//
// So weight is now a prop with a fixed vocabulary, and the vocabulary is small on
// purpose:
//
//   primary    the one thing this screen is for. At most one per screen.
//   secondary  a real action, subordinate to the primary one.
//   ghost      chrome — toolbar and header affordances that shouldn't draw the eye.
//   danger     destructive, and it says so at rest rather than only on hover.
//   link       inline in prose, where a bordered control would break the sentence.
//
// `loading` exists because "did my click register" is the question every async
// button in this app was silently failing to answer: it disables the control and
// swaps the leading icon for a spinner, so the answer is on the button itself
// rather than in a toast that may not have arrived yet.
export default function Button({
  variant = "secondary",
  size = "md",
  icon,
  iconRight,
  loading = false,
  disabled = false,
  className = "",
  children,
  ...rest
}) {
  const cls = [
    "ui-btn",
    `ui-btn-${variant}`,
    size !== "md" && `ui-btn-${size}`,
    loading && "is-loading",
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    // `type` before the spread so a caller can still ask for a submit button.
    // The default is deliberate: a bare <button> inside a <form> submits it, and
    // "the copy button reloaded the page" is a bug nobody looks for.
    <button type="button" className={cls} disabled={disabled || loading} {...rest}>
      {loading ? <Spinner /> : icon}
      {children != null && children !== false && <span className="ui-btn-label">{children}</span>}
      {iconRight}
    </button>
  );
}

// Inline rather than in icons.jsx: it is not an icon anyone picks, it is this
// component's loading state.
function Spinner() {
  return (
    <svg className="ui-spinner" width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
      <circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" strokeWidth="2" opacity="0.25" />
      <path
        d="M8 1.5a6.5 6.5 0 0 1 6.5 6.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  );
}

// An icon-only button. Split out rather than left as `size="icon"` on Button
// because the two have different contracts: this one *requires* a label, since
// there is no text to read it from. Passing the label rather than a `title` alone
// means the name survives on touch and in the accessibility tree, where a tooltip
// does not exist at all.
export function IconButton({ label, variant = "ghost", className = "", ...rest }) {
  return (
    <Button
      variant={variant}
      size="icon"
      aria-label={label}
      title={rest.title || label}
      className={className}
      {...rest}
    />
  );
}
