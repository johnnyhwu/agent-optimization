import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

const mockApi = path.resolve(import.meta.dirname, "api.js");

export default defineConfig({
  root: path.resolve(import.meta.dirname, ".."),
  plugins: [
    react(),
    {
      name: "mock-api",
      enforce: "pre",
      async resolveId(source, importer) {
        if (!/(^|\/)api\.js$/.test(source)) return null;
        if (importer && importer.includes("/harness/")) return null;
        return mockApi;
      },
    },
  ],
  optimizeDeps: { entries: ["harness/index.html"] },
  server: { port: 5199, strictPort: true },
});
