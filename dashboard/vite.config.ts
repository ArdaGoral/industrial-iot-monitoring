import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Geliştirmede dashboard 5173'te, backend 8000'de çalışır.
// Proxy sayesinde tarayıcı her şeyi 5173'ten ister; Vite /api ve /ws'yi
// backend'e iletir. Böylece kodda adres yazmaya ve CORS'a gerek kalmaz.
const BACKEND = "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": BACKEND,
      "/ws": { target: BACKEND.replace(/^http/, "ws"), ws: true },
    },
  },
});
