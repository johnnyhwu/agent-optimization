import React, { useState } from "react";
import { IconChevronDown } from "../icons.jsx";

// Form structure, in three pieces.
//
// The forms in this app were vertical dumps: `<div className="field"><label>` ×62,
// with grouping expressed only by an occasional uppercase `.cfg-section` heading
// and help text as a loose `.hint` div that no control was actually associated
// with. The run-config dialog put eleven of those in a row, most of them disabled
// and captioned with an environment-variable name, in front of someone who wanted
// to press one button.
//
// Field ties label, control, help and error together so the association is
// structural rather than positional. FormSection groups. Disclosure is the one
// that changes how the forms *feel*: it lets a screen open on its summary and keep
// the depth one click away, instead of leading with everything it can do.

export default function Field({
  label,
  htmlFor,
  help,
  error,
  required,
  hint,
  children,
  className = "",
}) {
  return (
    <div className={`ui-field ${className}`.trim()}>
      {label && (
        <label className="ui-field-label" htmlFor={htmlFor}>
          {label}
          {required && <span className="ui-field-req" aria-hidden="true">*</span>}
          {hint && <span className="ui-field-hint">{hint}</span>}
        </label>
      )}
      <div className="ui-field-control">{children}</div>
      {/* Help sits under the control and stays there.
          Both carry an id derived from the control's, so a caller that gave one
          can point `aria-describedby` at them: help under a box is read by
          anyone who can see it and by nobody who cannot, unless it is wired up.
          Placeholder-only guidance disappears exactly when the user starts
          typing, which is when they were reading it. */}
      {help && !error && (
        <p className="ui-field-help" id={htmlFor ? `${htmlFor}-help` : undefined}>
          {help}
        </p>
      )}
      {error && (
        <p className="ui-field-error" id={htmlFor ? `${htmlFor}-error` : undefined}>
          {error}
        </p>
      )}
    </div>
  );
}

export function FormSection({ id, title, description, aside, children, className = "" }) {
  return (
    // `id` so a long form can be jumped into. A section that is a scroll target
    // needs `scroll-margin-top` at its call site, or a sticky top bar lands on
    // top of the heading the jump was aimed at.
    <section id={id} className={`ui-form-section ${className}`.trim()}>
      {(title || aside) && (
        <div className="ui-form-section-head">
          <div>
            <h4 className="ui-form-section-title">{title}</h4>
            {description && <p className="ui-form-section-desc">{description}</p>}
          </div>
          {aside && <div className="ui-form-section-aside">{aside}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

// Progressive disclosure. `summary` is what the closed state says — make it a
// statement of what will happen, not a label: "Advanced settings" alone tells a
// developer nothing about whether they need to open it, whereas "Using the
// environment defaults" tells them they don't.
// `aside` is the closed state's escape hatch: something inside needs attention
// and the reader has no reason to open it. Named and placed like FormSection's
// `aside` — same word, same corner — so a badge means the same thing wherever it
// turns up. It renders open or closed, because a mark that vanished on opening
// would take the explanation of *why you opened this* with it.
export function Disclosure({
  summary,
  detail,
  aside,
  defaultOpen = false,
  icon,
  children,
  className = "",
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className={`ui-disclosure${open ? " is-open" : ""} ${className}`.trim()}>
      <button
        type="button"
        className="ui-disclosure-trigger"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <IconChevronDown size={15} className="ui-disclosure-chev" />
        {icon}
        <span className="ui-disclosure-summary">{summary}</span>
        {detail && <span className="ui-disclosure-detail">{detail}</span>}
        {aside && <span className="ui-disclosure-aside">{aside}</span>}
      </button>
      {open && <div className="ui-disclosure-body">{children}</div>}
    </div>
  );
}
