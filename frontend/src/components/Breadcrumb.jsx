import React from "react";
import { href } from "../useHashRoute.js";
import { IconChevronRight } from "./icons.jsx";

// §6.13 breadcrumb: one click back to the run history, one more to the set list.
//
// It renders only where there is somewhere to go back to. At the top tier the
// single "Eval Sets" crumb said nothing the page heading below it didn't, and a
// trail of one is not a trail.
//
// The last crumb deliberately does *not* repeat the incorrect mode: the detail
// view's own meta line already carries it, and a crumb's job is to name the
// place, not to describe it.
export default function Breadcrumb({ route, evalSet }) {
  if (route.tier !== "runs" && route.tier !== "detail") return null;

  const crumbs = [{ label: "Eval sets", to: href.evaluation() }];
  crumbs.push({
    label: evalSet ? evalSet.name : "…",
    to: route.tier === "detail" && evalSet ? href.evalSet(evalSet.id) : null,
  });
  if (route.tier === "detail") {
    const n = route.runIds.length;
    crumbs.push({ label: n === 1 ? "1 run" : `${n} runs compared`, to: null });
  }

  return (
    <nav className="breadcrumb" aria-label="Breadcrumb">
      <ol>
        {crumbs.map((c, i) => (
          <li key={i}>
            {i > 0 && (
              <span className="sep" aria-hidden="true">
                <IconChevronRight size={14} />
              </span>
            )}
            {c.to ? (
              <a href={c.to} title={c.label}>{c.label}</a>
            ) : (
              <span
                className="current"
                title={c.label}
                aria-current={i === crumbs.length - 1 ? "page" : undefined}
              >
                {c.label}
              </span>
            )}
          </li>
        ))}
      </ol>
    </nav>
  );
}
