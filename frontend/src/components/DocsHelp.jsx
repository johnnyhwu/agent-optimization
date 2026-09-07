import React from "react";
import { IconInfo } from "./icons.jsx";
import { href } from "../useHashRoute.js";

// The "?" beside a field that names part of the agent-server contract.
//
// Three things it settles, in one place, for every screen that asks for an
// agent server — the run dialog, the optimize wizard, the playground's connect
// bar and the contract checker. They asked for the same four values and only
// one of them offered any help at all, which made the help read as a property
// of that screen rather than of the field.
//
//   * **It is a deep link, not a link to the front of the docs.** Somebody
//     clicking this has a specific question, and landing them on a table of
//     contents makes them find the answer twice.
//   * **It opens a new tab.** This is a reference lookup in the middle of
//     filling in a form: navigating the tab away throws out everything typed so
//     far, including a credential that is never sent back to the browser and
//     would have to be typed again. The form stays where it is and the answer
//     arrives beside it.
//   * **The name says both.** "opens in a new tab" is in the accessible name,
//     not only in the tooltip, because a tooltip does not exist on touch and a
//     link that opens a tab without warning is disorienting either way.
export default function DocsHelp({ anchor, label, doc = "agent-server" }) {
  return (
    <a
      className="docs-help ui-btn ui-btn-ghost ui-btn-icon"
      href={href.docs(doc, anchor)}
      target="_blank"
      rel="noopener noreferrer"
      title={`${label} — opens the documentation in a new tab`}
      aria-label={`${label} (opens in a new tab)`}
    >
      <IconInfo size={14} />
    </a>
  );
}
