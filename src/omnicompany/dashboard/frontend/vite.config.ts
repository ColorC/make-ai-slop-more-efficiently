import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
// 共用 wiki 核（唯一正本在 webworks 仓）: markdown 材料渲染走它, 不再自带极简实现。
// 依赖按目录就近解析(wiki-core 自带 node_modules), 本工程无需加装 markdown-it 系。
const wikiCore = resolve(here, '../../../../packages/wiki-core')

const serverPort = Number(process.env.OMNI_VITE_PORT || '5173')
const dashboardProxyPort = process.env.OMNI_DASHBOARD_PROXY_PORT
  || process.env.OMNI_E2E_DASHBOARD_PORT
  || '8200'
// 把运行中的 walker-game 开发服务挂到 dashboard 同源路径 /walker-game/, 这样审阅 iframe 与
// dashboard 同源, 圈选元素(读 iframe.contentDocument)才不被浏览器跨域拦截。游戏侧用
// `npm run dev:dashboard` 以 base=/walker-game/ 启动(默认 5176)。
const walkerGameTarget = process.env.OMNI_WALKER_GAME_URL || 'http://127.0.0.1:5176'
// Vilo 当前 demo 是 tabletop-simulator 的静态 http.server, 不是以 /vilo-demo/ 为 base 的 Vite app。
// 因此代理时要 strip prefix: /vilo-demo/turn-ui.js -> http://127.0.0.1:8892/turn-ui.js。
const viloDemoTarget = process.env.OMNI_VILO_DEMO_URL || 'http://127.0.0.1:8892'
const viloOsTarget = process.env.OMNI_VILO_OS_URL || 'http://127.0.0.1:5186'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@wiki-core': wikiCore,
    },
  },
  server: {
    port: serverPort,
    // 默认 fs.allow 只有本工程根; wiki-core 在仓外, dev 模式要显式放行。
    fs: { allow: [here, wikiCore] },
    proxy: {
      '/api': {
        target: `http://localhost:${dashboardProxyPort}`,
        changeOrigin: true,
        // Terminal and native-chat transports both upgrade below /api.
        // Without WS forwarding, the dev UI renders xterm but remains stuck in
        // "connecting", which masks production PTY/resume regressions.
        ws: true,
      },
      // FileBridge intentionally lives outside /api so the same route can be
      // consumed by Dashboard and LOFA. Keep dev/E2E behavior identical to the
      // production same-origin server instead of returning Vite's SPA fallback.
      '/lofa/file-bridge': {
        target: `http://localhost:${dashboardProxyPort}`,
        changeOrigin: true,
      },
      '/walker-game': {
        target: walkerGameTarget,
        changeOrigin: true,
        ws: true,
      },
      '/vilo-demo': {
        target: viloDemoTarget,
        changeOrigin: true,
        ws: true,
        rewrite: (path) => path.replace(/^\/vilo-demo/, '') || '/',
      },
      '/vilo-os': {
        target: viloOsTarget,
        changeOrigin: true,
        ws: true,
      },
    },
  },
  build: {
    outDir: '../static',
    emptyOutDir: true,
    // esbuild 0.21 corrupts xterm 6's requestMode enum initializer in a
    // minified production chunk (undeclared variable at runtime). Terser keeps
    // the lazy terminal chunk compact without changing xterm's semantics.
    minify: 'terser',
    // Terser ranks short identifier names from each chunk's character
    // frequencies.  Vite's multi-worker scheduling made that ranking and the
    // resulting content hashes drift between otherwise identical builds on
    // this large graph.  A single worker keeps public/source builds byte-stable.
    terserOptions: { maxWorkers: 1 },
    chunkSizeWarningLimit: 800,
    rollupOptions: {
      output: {
        // Keep Vite/Rollup's content-hashed filenames. Naming chunks only by
        // their module set makes changed module contents reuse an old URL;
        // browsers can then combine a fresh entry with stale React-dependent
        // chunks and fail with React invariant 321.
        // 注意: 只把「静态可达」的大库放进 manualChunks(分文件利于缓存)。
        // 纯动态引入的库(cytoscape/react-syntax-highlighter/xterm)**不要**写在这里 ——
        // 把动态库强行塞进具名 manualChunk 会把它钉回入口的静态图、被 index.html modulepreload,
        // 反而抵消懒加载(Vite 已知坑)。让 Rollup 按动态 import 自动拆它们的 async chunk 即可。
        manualChunks(id: string) {
          const match = /[\\/]node_modules[\\/](.*)/.exec(id)
          if (!match) return
          const pkg = match[1].replace(/\\/g, '/')
          if (/^(react|react-dom|scheduler|zustand|use-sync-external-store)\//.test(pkg)) return 'vendor'
          if (pkg.startsWith('@monaco-editor/')) return 'monaco'
          if (pkg.startsWith('katex/') || pkg.startsWith('remark-math/') || pkg.startsWith('rehype-katex/')) return 'katex'
          if (pkg.startsWith('reactflow/') || pkg.startsWith('@reactflow/')) return 'reactflow'
          if (pkg.startsWith('kbar/')) return 'kbar'
          if (pkg.startsWith('react-markdown/') || pkg.startsWith('remark-gfm/') || pkg.startsWith('unist-util-visit/')) return 'remark'
        },
      },
    },
  },
})
