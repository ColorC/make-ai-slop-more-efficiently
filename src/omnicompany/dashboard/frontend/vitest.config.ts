/// <reference types="vitest" />
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
// 与 vite.config.ts 同源的 wiki-core alias — vitest 配置独立于 vite 配置, 不会自动继承,
// 否则任何 import @wiki-core/* 的组件 (markdown 材料渲染) 在单测里解析不到。
const wikiCore = resolve(here, '../../../../packages/wiki-core')
const materialCore = resolve(here, '../../../../packages/material-core')
const materialClient = resolve(here, '../../../../packages/material-client')
const materialNoteCore = resolve(here, '../../../../packages/material-note-core')
const overlayNotes = resolve(here, '../notes-web/src')
const bsideMaterialPlugin = resolve(here, '../../../../packages/material-http-plugin/index.ts')

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@wiki-core': wikiCore,
      '@material-core': materialCore,
      '@omni/material-core': materialCore,
      '@material-client': materialClient,
      '@omni/material-client': materialClient,
      '@material-note-core': materialNoteCore,
      '@bside-material-plugin': bsideMaterialPlugin,
      '@overlay-notes': overlayNotes,
      '@xyflow/react': resolve(here, 'node_modules/@xyflow/react'),
      'lucide-react': resolve(here, 'node_modules/lucide-react'),
      react: resolve(here, 'node_modules/react'),
      'react-dom': resolve(here, 'node_modules/react-dom'),
    },
    dedupe: ['react', 'react-dom', 'lucide-react', '@xyflow/react'],
  },
  test: {
    globals: true,
    environment: 'jsdom',
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
    // exclude e2e tests — those run via Playwright, separate runner
    exclude: ['node_modules', 'tests/e2e/**'],
    // 配合 fake timer (wsAutoReconnect 退避协议测试要时间快进)
    testTimeout: 5_000,
  },
})
