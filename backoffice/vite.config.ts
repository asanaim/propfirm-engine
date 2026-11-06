/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The back office calls the API under /api, like the portal: Vite forwards
// it in development, the web server (Caddy) in production (one origin).
const api = process.env.PROPFIRM_API_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    strictPort: true, // the Keycloak `backoffice` client allows only this address
    proxy: {
      "/api": { target: api, rewrite: (path) => path.replace(/^\/api/, "") },
    },
  },
  // staff-only, behind sign-in: one bundle (react-admin and MUI ≈ 1.1 MB) is fine
  build: { sourcemap: true, chunkSizeWarningLimit: 1500 },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
    include: ["src/**/*.test.{ts,tsx}", "scripts/**/*.test.ts"],
  },
});
