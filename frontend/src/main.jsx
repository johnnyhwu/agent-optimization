import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import { initAuth } from "./auth.js";
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

// Identity is settled before the first render. In keycloak mode this navigates
// away to the login page and comes back, so nothing below runs until the user is
// known — which is why no component has to handle "not signed in yet".
// In fake mode it resolves immediately.
initAuth().then(
  () => createRoot(document.getElementById("root")).render(<App />),
  (err) => {
    // A failure here means Keycloak itself is unreachable or misconfigured.
    // Rendering the app would show an empty shell whose every request 401s, so
    // say what happened instead.
    document.getElementById("root").innerHTML =
      `<div style="padding:2rem;font:14px system-ui">` +
      `<h1 style="font-size:1.1rem">Sign-in unavailable</h1>` +
      `<p>Could not reach the identity provider.</p>` +
      `<pre style="white-space:pre-wrap">${String(err && err.message ? err.message : err)}</pre>` +
      `</div>`;
  }
);
