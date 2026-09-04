import React from "react";
import { href } from "../useHashRoute.js";
import { IconChevronRight } from "./icons.jsx";

// §6.13 breadcrumb: one click back to the run history, one more to the set list.
//
// It lives in the top bar, and it is the *only* thing there that says where you
// are. Three separate elements used to say it at once — the top bar's section
// title, this trail on its own row below it, and the page's own <h1> — which on
// the run history read "Evaluation", then "Eval sets › Billing agent
// regression", then "Billing agent regression" again, in about 200px of chrome
// before any content. The rail already marks the section (visually and with
// aria-current), so the top bar's copy of it was pure duplication; this trail
// says strictly more, and being in a sticky bar it now survives scrolling,
// which it did not when it sat in the page.
//
// The <h1> below is kept on purpose and is not a fourth statement: a small
// muted crumb and a page title are the conventional pair, and the crumb is the
// route while the heading is the thing.
//
// A trail of one is not a trail, and it renders nothing. On the top tier — and
// on Playground, Optimize and Settings — the only crumb there is to draw is the
// page's own name, which the <h1> is already saying about ninety pixels below
// it. Moving the duplication from the section title into the crumb would have
// been no better than leaving it where it was; it would have been worse, since
// the two would now match word for word. The rail marks the section, the
// heading names the page, and the bar stays quiet until there is somewhere to
// go back to.
//
// The last crumb deliberately does *not* repeat the incorrect mode: the detail
// view's own meta line already carries it, and a crumb's job is to name the
// place, not to describe it.
export default function Breadcrumb({ route, evalSet }) {
  // Only the evaluation route has depth today. A section that grows some adds
  // its crumbs here; until then it has a heading and that is all it needs.
  const crumbs = [];
  if (route.section === "evaluation") {
    crumbs.push({
      label: "Eval sets",
      to: route.tier === "sets" ? null : href.evaluation(),
    });
    if (route.tier === "runs" || route.tier === "detail") {
      crumbs.push({
        label: evalSet ? evalSet.name : "…",
        to: route.tier === "detail" && evalSet ? href.evalSet(evalSet.id) : null,
      });
    }
    if (route.tier === "detail") {
      const n = route.runIds.length;
      crumbs.push({ label: n === 1 ? "1 run" : `${n} runs compared`, to: null });
    }
  }

  if (crumbs.length < 2) return null;

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
