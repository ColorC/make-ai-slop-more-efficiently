/// <reference types="vitest" />
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { resolve } from 'node:path'
// 与 vite.config.ts 同一份同级仓路径(vitest 配置不会自动继承 vite 配置的 alias)。
import {
  bsideMaterialPlugin,
  frontendRoot as here,
  materialClient,
  materialCore,
  materialNoteCore,
  overlayNotes,
  wikiCore,
} from './siblingRepoPaths'

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
