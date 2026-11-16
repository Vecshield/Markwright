import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev proxy: the dashboard is a pure client of the REST API (design §3 —
// every surface calls the one API; nothing bypasses it).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: Object.fromEntries(
      ["/v1", "/healthz", "/readyz", "/metrics"].map((p) => [
        p,
        { target: process.env.VITE_API_URL ?? "http://localhost:8000", changeOrigin: true },
      ]),
    ),
  },
});
