import React, { useCallback, useEffect, useState } from "react";
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
// Until there are two, the column was also a 220px strip holding a single link
// to the page you were already on, next to a form six groups long that offered
// no way to reach its middle. So the panel hands its groups up and the column
// lists them: the same navigation the page always needed, in the space that was
// already reserved for navigation. Each carries the number of settings in it
// this developer has an opinion about, which is the one question the page's
// scroll length makes expensive to answer any other way.
//
// Reached from the user menu, not from the side rail. The rail is what the
// product *does*; this is about the person signed in, and putting it there would
// have made it look like a fourth section.
const PANELS = [
  {
    id: "defaults",
    label: "Defaults",
    description: "What every form opens with",
    render: (props) => <DefaultsPanel {...props} />,
  },
];

export default function SettingsSection({ route, unseen = 0 }) {
  const current = PANELS.find((p) => p.id === route.panel) || PANELS[0];
  const [outline, setOutline] = useState([]);
  const active = useScrollSpy(outline.map((g) => `settings-group-${g.id}`));

  // Identity-stable, or the panel's reporting effect re-runs on every render of
  // this one.
  const onOutline = useCallback((groups) => setOutline(groups), []);

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
              {panel.id === current.id && outline.length > 0 && (
                <ul className="settings-jump">
                  {outline.map((group) => {
                    const id = `settings-group-${group.id}`;
                    return (
                      <li key={group.id}>
                        <a
                          className={`settings-jump-item${id === active ? " active" : ""}`}
                          href={`#${id}`}
                          aria-current={id === active ? "true" : undefined}
                          onClick={(e) => {
                            // The app's address is the hash, so a bare fragment
                            // link would replace the route with `#settings-group-…`
                            // and drop the page it was scrolling within.
                            e.preventDefault();
                            document
                              .getElementById(id)
                              ?.scrollIntoView({ behavior: "smooth", block: "start" });
                          }}
                        >
                          <span className="settings-jump-label">{group.label}</span>
                          {group.count > 0 && (
                            <span
                              className="settings-jump-count"
                              title={`${group.count} overridden`}
                            >
                              {group.count}
                            </span>
                          )}
                        </a>
                      </li>
                    );
                  })}
                </ul>
              )}
            </li>
          ))}
        </ul>
      </nav>
      <div className="settings-body">{current.render({ onOutline })}</div>
    </div>
  );
}

// Which section the reader is in, from the browser rather than from arithmetic.
//
// The top margin is what makes it read correctly: a heading is "current" from
// the moment it reaches the top bar, not from the moment it enters the window,
// so the highlight changes where a person would say it changed. The bottom
// margin keeps only the topmost visible heading in play, otherwise every
// section below it also qualifies and the last one wins.
function useScrollSpy(ids) {
  const [active, setActive] = useState(null);
  const key = ids.join("|");

  useEffect(() => {
    const targets = key ? key.split("|") : [];
    if (targets.length === 0 || typeof IntersectionObserver !== "function") {
      setActive(null);
      return undefined;
    }
    // At the top of the page every heading is *below* the band, so nothing has
    // crossed it yet. That is still "the reader is in the first group", and a
    // list with nothing lit reads as a list that does not track you.
    setActive((current) => (targets.includes(current) ? current : targets[0]));
    const scroller = document.querySelector(".main");
    // The last group can never reach the band: the page stops scrolling with it
    // still halfway down, so a strict reading of "what is at the top" lights the
    // second-to-last section forever and clicking the last jump link appears to
    // do nothing. At the bottom of the page the reader is in the last group,
    // whatever is level with the top edge.
    const atBottom = () =>
      scroller &&
      scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 8;

    const seen = new Map();
    const settle = () => {
      if (atBottom()) return setActive(targets[targets.length - 1]);
      const first = targets.find((id) => seen.get(id));
      // No match part-way down means every heading has scrolled past the top of
      // the band; the reader is inside the last one they crossed, so it stays
      // lit rather than the highlight disappearing.
      if (first) setActive(first);
      return undefined;
    };

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) seen.set(entry.target.id, entry.isIntersecting);
        settle();
      },
      { rootMargin: "-60px 0px -70% 0px", threshold: 0 }
    );
    for (const id of targets) {
      const el = document.getElementById(id);
      if (el) observer.observe(el);
    }
    scroller?.addEventListener("scroll", settle, { passive: true });
    return () => {
      observer.disconnect();
      scroller?.removeEventListener("scroll", settle);
    };
  }, [key]);

  return active;
}
