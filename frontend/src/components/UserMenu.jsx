import React from "react";
import { isKeycloak } from "../app_config.js";
import { logout } from "../auth.js";
import { href, navigate } from "../useHashRoute.js";
import ThemeToggle from "./ThemeToggle.jsx";
import Menu, { MenuItem, MenuSeparator } from "./ui/Menu.jsx";
import { IconChevronDown, IconGear, IconUsers } from "./icons.jsx";

// Who is signed in, and the two things you can do about it.
//
// This was a native <select> sitting in the top bar next to the words "Signed in
// as" and a coloured initial — three elements spelling out one fact, one of them
// an unstyled OS control that looked like nothing else in the product. A native
// select in a top bar is about the strongest "this was assembled, not designed"
// signal an interface can send.
//
// The user switcher only exists in the demo identity mode, where there is a
// directory to switch between; against a real identity provider the endpoint
// returns nothing and there is only a name and a way out.
//
// Settings is here rather than in the side rail, and in both identity modes.
// The rail lists what the product does; a page of one developer's own defaults
// is about the person signed in, and a fourth entry there would have read as a
// fourth section. `attention` is the count of things on that page worth a look —
// settings added since they last visited, and overrides whose deployment value
// has moved underneath them — shown as a dot rather than a number, because the
// number is not the point and the page will say it properly.
// The avatar carries white initials, so every one of these is a background with
// text on it. The 500-weight ramp this started from measured 2.15:1 (amber) to
// 4.47:1 (indigo) against white — all six below AA, the worst of them barely
// legible. These are the 700/800 weights of the same six hues: same palette,
// same distinguishability, 5.0:1 or better across the set. They live in JS
// rather than CSS, so `css_contract.test.js` cannot see them; that is why the
// ratios are written down here.
const AVATAR_COLORS = ["#4f46e5", "#0369a1", "#047857", "#b45309", "#be185d", "#6d28d9"];

export function avatarColor(name) {
  let h = 0;
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) % AVATAR_COLORS.length;
  return AVATAR_COLORS[h];
}

export default function UserMenu({ subject, users, onSwitchUser, attention = 0 }) {
  const canSwitch = !isKeycloak && users.length > 1;

  return (
    <div className="userbox">
      <ThemeToggle />
      <Menu
        align="end"
        width={210}
        trigger={
          <button className="usermenu-trigger" title={`Signed in as ${subject}`}>
            <span className="avatar" style={{ background: avatarColor(subject) }}>
              {subject.slice(0, 1)}
              {attention > 0 && <span className="avatar-dot" aria-hidden="true" />}
            </span>
            <span className="usermenu-name">{subject}</span>
            <IconChevronDown size={14} className="usermenu-chev" />
          </button>
        }
      >
        <div className="usermenu-head">
          <div className="usermenu-head-name">{subject}</div>
          <div className="usermenu-head-sub">
            {isKeycloak ? "Signed in" : "Demo identity"}
          </div>
        </div>
        <MenuSeparator />

        <MenuItem onClick={() => navigate(href.settings())}>
          <IconGear size={13} /> Settings
          {attention > 0 && <span className="menu-dot" aria-hidden="true" />}
        </MenuItem>

        {canSwitch && (
          <>
            <div className="usermenu-label">
              <IconUsers size={12} /> Switch user
            </div>
            {users.map((u) => (
              <MenuItem
                key={u}
                onClick={() => u !== subject && onSwitchUser(u)}
                aria-current={u === subject ? "true" : undefined}
                className={u === subject ? "is-current" : undefined}
              >
                {u}
              </MenuItem>
            ))}
          </>
        )}

        {isKeycloak && (
          <MenuItem variant="danger" onClick={logout}>
            Sign out
          </MenuItem>
        )}
      </Menu>
    </div>
  );
}
