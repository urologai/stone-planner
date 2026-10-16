import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: `npm run dev` with the API on :8000 (see docs/deploy.md).
// Build: dist/ is copied into the Docker image and served by FastAPI.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": "http://localhost:8000" },
  },
  build: { outDir: "dist", sourcemap: false },
});
