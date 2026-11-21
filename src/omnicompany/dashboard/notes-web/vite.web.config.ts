// OmniDashboard 唯一画布前端(notes-web)纯网页构建。
// 2026-08-17 从已退役 overlay-shell 仓原样迁入(vite.web.config.ts 语义不变)。
// 出静态到 dist-web/, 经 omnicompany dashboard(8210)在 /lofa/overlay/app/ 下服务。
// base "./" → 资源相对引用, 可挂任意子路径。
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 2026-08-17 修「画布桌面壳 notes_canvas_get not allowed by ACL」: 真实 Tauri 宿主
// (Omnicompany Desktop)冻结 __TAURI_INTERNALS__, 运行时垫片(tauri-web-shim.ts)的
// invoke 字段赋值会被吞掉, notes_* 直打宿主 Rust 撞 ACL。构建期直接把裸 import
// "@tauri-apps/api/core" 整体改道到 src/tauriCoreBridge.ts, 不依赖运行时可变性。
const tauriCoreBridgePath = fileURLToPath(new URL("./src/tauriCoreBridge.ts", import.meta.url));

export default defineConfig({
  resolve: {
    alias: [
      { find: "@tauri-apps/api/core", replacement: tauriCoreBridgePath },
    ],
  },
  plugins: [
    react(),
    {
      name: "notes-entry-fetch-priority",
      transformIndexHtml: {
        order: "post",
        handler(html) {
          return html.replace(
            '<script type="module" crossorigin',
            '<script type="module" crossorigin fetchpriority="high"',
          );
        },
      },
    },
  ],
  base: "./",
  build: {
    outDir: "dist-web",
    emptyOutDir: true,
    target: "es2020",
    rollupOptions: {
      input: {
        page: "notes-web.html",
        "notes-embed": "src/notes-embed.ts",
      },
      preserveEntrySignatures: "strict",
      output: {
        // 主入口带哈希(2026-08-16): 固定文件名 + 浏览器 304 缓存会在部署交界处造成
        // "新 html 配旧 js" 的版本错配 → 启动崩死在加载门且无可见报错(远端卡死根因嫌疑)。
        entryFileNames: (chunk) => chunk.name === "notes-embed"
          ? "assets/notes-embed.js"
          : "assets/notes-web-[hash].js",
        chunkFileNames: "assets/[name]-[hash].js",
        assetFileNames: "assets/[name][extname]",
      },
    },
  },
});
