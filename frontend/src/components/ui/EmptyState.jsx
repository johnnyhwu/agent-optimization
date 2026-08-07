import React from "react";

// What a screen says when it has nothing to show.
//
// The old answer was a line of grey text — and in the worst case, on the very
// first screen a new user sees, that line was *"No eval sets yet. Upload one, or
// run the seed script."* Telling someone to go and run a developer script is the
// single clearest signal in this frontend that it was a demo rather than a
// product.
//
// So an empty state is now a proper unit: a mark, a title that names the state,
// one sentence that says what to do about it, and the button that does it. The
// action is the point — an empty state without one is just a smaller way of
// saying nothing.
export default function EmptyState({
  icon,
  title,
  children,
  action,
  secondaryAction,
  size = "md",
  className = "",
}) {
  return (
    <div className={`ui-empty ui-empty-${size} ${className}`.trim()}>
      {icon && <div className="ui-empty-icon">{icon}</div>}
      {title && <h3 className="ui-empty-title">{title}</h3>}
      {children && <p className="ui-empty-body">{children}</p>}
      {(action || secondaryAction) && (
        <div className="ui-empty-actions">
          {action}
          {secondaryAction}
        </div>
      )}
    </div>
  );
}

// The in-column version: the three-column detail view needs "nothing selected"
// and "nothing matched" messages that sit inside a panel, where the full empty
// state's vertical padding would push the panel taller than its own content.
export function InlineEmpty({ children, className = "" }) {
  return <div className={`ui-empty-inline ${className}`.trim()}>{children}</div>;
}
