/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The portal calls the API under /api: in development Vite forwards it to
// the API process; in production the web server (Caddy) does the same, so
// the browser only ever talks to one origin (no CORS).
const api = process.env.PROPFIRM_API_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true, // the Keycloak client allows only this redirect address
    proxy: {
      "/api": { target: api, rewrite: (path) => path.replace(/^\/api/, "") },
    },
  },
  build: { sourcemap: true },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
    include: ["src/**/*.test.{ts,tsx}", "scripts/**/*.test.ts"],
  },
});
