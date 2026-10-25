import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// `npm run dev` serves the client on :5173 and forwards /api (WebSocket
// included) to a server on :8770, so the session cookie stays same-origin.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": { target: "http://127.0.0.1:8770", ws: true },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    css: false,
  },
});
