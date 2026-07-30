import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
// Bundled, not fetched from a CDN: the app is deployed as docker-compose behind
// whatever network the developer has, and styles.css names these three faces by
// family. Variable where one exists, so the weight range costs one file.
import "@fontsource-variable/inter";
import "@fontsource-variable/space-grotesk";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/600.css";
import "./styles.css";

// Set the theme before first paint to avoid a flash.
const saved = localStorage.getItem("theme");
const theme =
  saved || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
document.documentElement.setAttribute("data-theme", theme);

createRoot(document.getElementById("root")).render(<App />);
