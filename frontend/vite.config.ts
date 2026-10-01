import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// In development the UI talks to the API on :8000 through this proxy.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8000" } },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
