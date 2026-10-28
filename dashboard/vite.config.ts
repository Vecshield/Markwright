import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev proxy: the dashboard is a pure client of the REST API (design §3 —
// every surface calls the one API; nothing bypasses it).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      "/v1": { target: process.env.VITE_API_URL ?? "http://localhost:8000", changeOrigin: true },
      "/healthz": { target: process.env.VITE_API_URL ?? "http://localhost:8000", changeOrigin: true },
    },
  },
});
