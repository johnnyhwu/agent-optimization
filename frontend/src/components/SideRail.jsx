import React, { useEffect, useState } from "react";
import { href } from "../useHashRoute.js";
import BrandMark from "./BrandMark.jsx";
import {
  IconBeaker,
  IconFileText,
  IconPanelLeft,
  IconSparkles,
  IconTarget,
} from "./icons.jsx";

// The app's top-level sections, as data. Adding one is an entry here plus a
// branch in App — not a new axis of state, which is what the old two-tab
// segmented control would have forced.
//
// `soon` marks a section that is designed for but not built: it appears so the
// shape of the product is visible, and says plainly that it isn't ready rather
// than 404ing on click.
export const SECTIONS = [
  { id: "evaluation", label: "Evaluation", icon: IconTarget, to: href.evaluation() },
  { id: "playground", label: "Playground", icon: IconBeaker, to: href.playground() },
  { id: "optimize", label: "Optimize", icon: IconSparkles, to: href.optimize() },
];

// Reference material, not a fourth thing the product does — so it sits at the
// foot of the rail rather than in the list above. Somebody arrives here from a
// "?" beside a field far more often than by looking for it, which is also why
// those links carry an anchor.
export const FOOTER_SECTIONS = [
  { id: "documentation", label: "Documentation", icon: IconFileText, to: href.docs() },
];

const KEY = "rail-collapsed";

export function useRailCollapsed() {
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem(KEY) === "1");
  useEffect(() => {
    document.documentElement.classList.toggle("rail-collapsed", collapsed);
  }, [collapsed]);
  return [collapsed, (v) => {
    localStorage.setItem(KEY, v ? "1" : "0");
    setCollapsed(v);
  }];
}

// Persistent left navigation. Real anchors, so middle-click, copy-link and
// browser history all work without any of our code.
export default function SideRail({ section, collapsed, onToggle }) {
  return (
    <nav className="rail" aria-label="Sections">
      {/* The name is on the link, not on the mark: collapsing the rail hides
          `.rail-brand-name`, and without this the only home link in the app
          would announce as nothing. */}
      <a className="rail-brand" href={href.evaluation()} aria-label="Skill Studio">
        <BrandMark className="logo" size={28} />
        <span className="rail-brand-name">Skill Studio</span>
      </a>

      <ul className="rail-list">
        {SECTIONS.map(({ id, label, icon: Icon, to, soon }) => (
          <li key={id}>
            {soon ? (
              // Not a link: there is nowhere to go yet. A disabled span keeps it
              // out of the tab order instead of offering a dead target.
              <span className="rail-item soon" title={`${label} — not built yet`}>
                <Icon size={17} />
                <span className="rail-label">{label}</span>
                <span className="rail-soon">Soon</span>
              </span>
            ) : (
              <a
                className={`rail-item${section === id ? " active" : ""}`}
                href={to}
                aria-current={section === id ? "page" : undefined}
                // Unconditional, because the rail collapses two ways and only
                // one of them is this state. Below 1100px a media query hides
                // `.rail-label` whatever the developer's saved preference is,
                // and the tooltip was tied to the preference — so on a narrow
                // window these were three unlabelled icons with no accessible
                // name at all. A title on an expanded item costs nothing; a
                // missing one costs the whole navigation.
                title={label}
              >
                <Icon size={17} />
                <span className="rail-label">{label}</span>
              </a>
            )}
          </li>
        ))}
      </ul>

      <ul className="rail-list rail-list-foot">
        {FOOTER_SECTIONS.map(({ id, label, icon: Icon, to }) => (
          <li key={id}>
            <a
              className={`rail-item${section === id ? " active" : ""}`}
              href={to}
              aria-current={section === id ? "page" : undefined}
              title={label}
            >
              <Icon size={17} />
              <span className="rail-label">{label}</span>
            </a>
          </li>
        ))}
      </ul>

      <button
        className="rail-collapse icon-btn"
        onClick={() => onToggle(!collapsed)}
        aria-label={collapsed ? "Expand navigation" : "Collapse navigation"}
        title={collapsed ? "Expand navigation" : "Collapse navigation"}
      >
        <IconPanelLeft size={16} />
        <span className="rail-label">Collapse</span>
      </button>
    </nav>
  );
}
