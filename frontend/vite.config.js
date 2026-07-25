import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// VITE_API_BASE (default http://localhost:8000) points the UI at the backend.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
});
