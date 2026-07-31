import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { createHash } from 'node:crypto'
import { fileURLToPath } from 'node:url'
import { dirname, relative, resolve } from 'node:path'

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

// Rollup's default content-hashed chunk names can form a hash-reference cycle:
// two semantically identical builds may receive different names when chunks
// reference one another.  Use the stable module set as the chunk identity so
// release reproducibility can be checked byte-for-byte without weakening the
// gate.  The digest also disambiguates repeated names such as `Editor`.
function stableChunkFileName(chunk: { name: string; moduleIds: string[] }): string {
  const moduleIds = [...chunk.moduleIds]
    .map((id) => relative(here, id).replaceAll('\\', '/'))
    .sort()
  const identity = moduleIds.length > 0 ? moduleIds.join('\n') : chunk.name
  const digest = createHash('sha256').update(identity).digest('hex').slice(0, 12)
  const safeName = chunk.name.replace(/[^A-Za-z0-9._-]+/g, '-') || 'chunk'
  return `assets/${safeName}-${digest}.js`
}

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
    },
  },
  build: {
    outDir: '../static',
    emptyOutDir: true,
    chunkSizeWarningLimit: 800,
    rollupOptions: {
      output: {
        chunkFileNames: stableChunkFileName,
        // 注意: 只把「静态可达」的大库放进 manualChunks(分文件利于缓存)。
        // 纯动态引入的库(cytoscape/react-syntax-highlighter/xterm)**不要**写在这里 ——
        // 把动态库强行塞进具名 manualChunk 会把它钉回入口的静态图、被 index.html modulepreload,
        // 反而抵消懒加载(Vite 已知坑)。让 Rollup 按动态 import 自动拆它们的 async chunk 即可。
        manualChunks: {
          'monaco': ['@monaco-editor/react'],
          'katex': ['katex', 'remark-math', 'rehype-katex'],
          'reactflow': ['reactflow', '@reactflow/core'],
          'kbar': ['kbar'],
          'remark': ['react-markdown', 'remark-gfm', 'unist-util-visit'],
        },
      },
    },
  },
})
