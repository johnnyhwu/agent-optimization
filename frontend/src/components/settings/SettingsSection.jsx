import React from "react";
import { href, navigate } from "../../useHashRoute.js";
import DefaultsPanel from "./DefaultsPanel.jsx";

// The settings section: a left column of panels, and whichever one the route
// names.
//
// One panel today. It has a column anyway because the column is the promise —
// the next thing that belongs to the person rather than to an eval set goes
// beside "Defaults" rather than being bolted onto it, and the shape of that is
// worth showing before there are two.
//
// Reached from the user menu, not from the side rail. The rail is what the
// product *does*; this is about the person signed in, and putting it there would
// have made it look like a fourth section.
const PANELS = [
  {
    id: "defaults",
    label: "Defaults",
    description: "What every form opens with",
    render: () => <DefaultsPanel />,
  },
];

export default function SettingsSection({ route, unseen = 0 }) {
  const current = PANELS.find((p) => p.id === route.panel) || PANELS[0];

  return (
    <div className="settings">
      <nav className="settings-nav" aria-label="Settings">
        <ul>
          {PANELS.map((panel) => (
            <li key={panel.id}>
              <a
                className={`settings-nav-item${panel.id === current.id ? " active" : ""}`}
                href={href.settings(panel.id)}
                aria-current={panel.id === current.id ? "page" : undefined}
                onClick={(e) => {
                  e.preventDefault();
                  navigate(href.settings(panel.id));
                }}
              >
                <span className="settings-nav-label">{panel.label}</span>
                {panel.id === "defaults" && unseen > 0 && (
                  <span className="settings-nav-dot" aria-label={`${unseen} new`} />
                )}
                <span className="settings-nav-desc">{panel.description}</span>
              </a>
            </li>
          ))}
        </ul>
      </nav>
      <div className="settings-body">{current.render()}</div>
    </div>
  );
}
