import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run dev` proxies API calls to a backend running on :8642.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://localhost:8642" } },
});
