import React from "react";
import { href, navigate } from "../../useHashRoute.js";
import { differsFromSystem } from "../../settings_fields.js";

// One line, on each form that a developer's saved defaults reach.
//
// The alternative was a marker on every field saying where its value came from,
// and on a form with eleven connection settings that is eleven pieces of
// furniture answering a question nobody asked twice. The distinction that
// actually needs drawing — mine versus the deployment's — belongs on the
// settings page, where an empty box with grey text through it *is* that
// distinction. Here it is enough to say the prefill is not the deployment's and
// point at where to change it.
//
// It says nothing at all when nothing was overridden, which is the common case
// and the one that deserves no chrome.
export default function DefaultsNotice({ defaults, systemDefaults }) {
  if (!differsFromSystem(defaults, systemDefaults)) return null;
  return (
    <div className="defaults-notice">
      Prefilled from your defaults ·{" "}
      <a
        href={href.settings()}
        onClick={(e) => {
          e.preventDefault();
          navigate(href.settings());
        }}
      >
        Edit
      </a>
    </div>
  );
}
