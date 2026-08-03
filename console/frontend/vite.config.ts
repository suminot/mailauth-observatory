import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// ローカル専用。API は同じマシンの FastAPI（8000）へプロキシする。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
