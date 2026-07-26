import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import "./styles.css";

// Set the theme before first paint to avoid a flash.
const saved = localStorage.getItem("theme");
const theme =
  saved || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
document.documentElement.setAttribute("data-theme", theme);

createRoot(document.getElementById("root")).render(<App />);
