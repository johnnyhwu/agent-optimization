import React from "react";
import { isKeycloak } from "../app_config.js";
import { logout } from "../auth.js";
import ThemeToggle from "./ThemeToggle.jsx";
import Menu, { MenuItem, MenuSeparator } from "./ui/Menu.jsx";
import { IconChevronDown, IconUsers } from "./icons.jsx";

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
const AVATAR_COLORS = ["#6366f1", "#0ea5e9", "#10b981", "#f59e0b", "#ec4899", "#8b5cf6"];

export function avatarColor(name) {
  let h = 0;
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) % AVATAR_COLORS.length;
  return AVATAR_COLORS[h];
}

export default function UserMenu({ subject, users, onSwitchUser }) {
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
