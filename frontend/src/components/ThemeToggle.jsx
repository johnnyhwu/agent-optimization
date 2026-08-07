import React, { useState } from "react";
import { IconMoon, IconSun } from "./icons.jsx";

// Flips <html data-theme> between light/dark and persists the choice.
export default function ThemeToggle() {
  const [theme, setTheme] = useState(
    () => document.documentElement.getAttribute("data-theme") || "light"
  );
  function toggle() {
    const next = theme === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("theme", next);
    setTheme(next);
  }
  return (
    <button className="ui-btn ui-btn-ghost ui-btn-icon" onClick={toggle} aria-label="Toggle theme" title="Toggle light/dark">
      {theme === "dark" ? <IconSun /> : <IconMoon />}
    </button>
  );
}
