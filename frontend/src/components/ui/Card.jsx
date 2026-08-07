import React from "react";

// The panel surface, in one place.
//
// `.card`, `.col`, `.toolbar`, `.runstatus`, `.agent-bar` and `.composer` each
// declared their own `background / border / border-radius / box-shadow` — six
// surfaces, four radii, three shadow choices. Not enough difference to mean
// anything, enough to look unconsidered.
//
// `interactive` is what an eval-set card is: a whole surface that opens something.
// It renders a <div role="button"> with keyboard handling rather than a bare
// onClick, because the old cards were plain divs — reachable by mouse only, which
// on a keyboard-driven internal tool meant the primary navigation of the home
// screen simply did not respond to Enter.
export default function Card({
  as: Tag = "div",
  padded = true,
  interactive = false,
  onClick,
  className = "",
  children,
  ...rest
}) {
  const cls = [
    "ui-card",
    padded && "is-padded",
    interactive && "is-interactive",
    className,
  ]
    .filter(Boolean)
    .join(" ");

  const behaviour =
    interactive && onClick
      ? {
          role: "button",
          tabIndex: 0,
          onClick,
          onKeyDown: (e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              onClick(e);
            }
          },
        }
      : { onClick };

  return (
    <Tag className={cls} {...behaviour} {...rest}>
      {children}
    </Tag>
  );
}

// A card's own header strip: title on the left, controls on the right. Used by
// the three columns of the detail view, where it is sticky so the filter stays
// reachable in a list of two hundred questions.
export function CardHeader({ title, count, actions, sticky = false, className = "" }) {
  return (
    <div className={`ui-card-head${sticky ? " is-sticky" : ""} ${className}`.trim()}>
      <div className="ui-card-head-title">
        <h4>{title}</h4>
        {count != null && <span className="ui-card-head-count">{count}</span>}
      </div>
      {actions && <div className="ui-card-head-actions">{actions}</div>}
    </div>
  );
}
