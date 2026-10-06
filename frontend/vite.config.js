import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the Vite server forwards API calls to FastAPI, so the app
// uses same-origin URLs and needs no CORS setup. In production FastAPI serves
// the built files from dist/ itself.
const backend = process.env.BACKEND_URL || "http://localhost:8001";
const apiPaths = ["/health", "/chat", "/ingest"];

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: Object.fromEntries(apiPaths.map((path) => [path, backend])),
  },
});
