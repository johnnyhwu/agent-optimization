import React from "react";
import { IconSearch } from "../icons.jsx";

// The filter bar that sits between a page header and a list.
//
// Two slots, because the two halves mean different things: the left is "narrow
// what I am looking at", the right is "do something with what I picked". Running
// them together in one flex row — which is what `.toolbar` did, with a lone
// `style={{ marginLeft: "auto" }}` on the button that needed to escape — put a
// primary action on the same line as a search box and made the row read as one
// undifferentiated strip of controls.
export default function Toolbar({ children, end, className = "" }) {
  return (
    <div className={`ui-toolbar ${className}`.trim()}>
      <div className="ui-toolbar-start">{children}</div>
      {end && <div className="ui-toolbar-end">{end}</div>}
    </div>
  );
}

// Search with the magnifier inside it. A bare input with placeholder text was
// indistinguishable from every other text field on the screen until you read it.
export function SearchInput({ className = "", ...rest }) {
  return (
    <div className={`ui-search ${className}`.trim()}>
      <IconSearch size={15} className="ui-search-icon" />
      <input type="search" {...rest} />
    </div>
  );
}

// The in-page filter pill. Kept visually distinct from the side rail on purpose:
// the rail means "go somewhere", this means "hide part of what is already here",
// and the two used to be the same component.
export function SegmentedControl({ value, onChange, options, size = "md", ariaLabel }) {
  return (
    <div
      className={`ui-segmented${size === "sm" ? " is-sm" : ""}`}
      role="tablist"
      aria-label={ariaLabel}
    >
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="tab"
          aria-selected={value === o.value}
          className={value === o.value ? "is-active" : ""}
          title={o.title}
          onClick={() => onChange(o.value)}
        >
          {o.label}
          {o.count != null && <span className="ui-segmented-count">{o.count}</span>}
        </button>
      ))}
    </div>
  );
}
