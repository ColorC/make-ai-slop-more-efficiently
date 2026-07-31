/// <reference types="vitest" />
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
// 与 vite.config.ts 同源的 wiki-core alias — vitest 配置独立于 vite 配置, 不会自动继承,
// 否则任何 import @wiki-core/* 的组件 (markdown 材料渲染) 在单测里解析不到。
const wikiCore = resolve(here, '../../../../packages/wiki-core')

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@wiki-core': wikiCore,
    },
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
