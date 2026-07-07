import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 开发时把 /api 代理到本地后端;构建产物进 dist,由 FastAPI 静态托管。
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: { outDir: "dist", emptyOutDir: true },
  server: {
    port: 5319,
    proxy: { "/api": "http://127.0.0.1:8330" },
  },
});
