import { defineConfig, devices } from '@playwright/test'

const dashboardPort = process.env.OMNI_E2E_DASHBOARD_PORT || '8200'
const frontendPort = process.env.OMNI_E2E_FRONTEND_PORT

export default defineConfig({
  testDir: './tests/e2e',
  // 真 chat session e2e 含 LLM 推理, 单 spec 默认 timeout 拉到 120s
  timeout: 120_000,
  fullyParallel: false,
  // 复用外部共享后端时强制单 worker: dev_reload 这类全局性 spec 会触发所有打开页面自刷新,
  // 多 worker 并行会把别的 spec 正在用的页面刷掉(2026-06-12 实测互扰)。
  workers: process.env.OMNI_E2E_DASHBOARD_PORT ? 1 : undefined,
  retries: 0,
  reporter: [['list']],
  // 自启 ccdaemon + dashboard 两进程, teardown 自杀; 端口已被占用时跳过 (用户手动起)
  globalSetup: './tests/e2e/global-setup.ts',
  globalTeardown: './tests/e2e/global-teardown.ts',
  webServer: frontendPort ? {
    command: `node ./node_modules/vite/bin/vite.js --host 127.0.0.1 --port ${frontendPort}`,
    url: `http://127.0.0.1:${frontendPort}`,
    reuseExistingServer: true,
    env: {
      OMNI_VITE_PORT: frontendPort,
      OMNI_DASHBOARD_PROXY_PORT: dashboardPort,
    },
  } : undefined,
  use: {
    baseURL: `http://127.0.0.1:${frontendPort || dashboardPort}`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    headless: true,
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
})
