// 免重启更新 — 真 UI 路径 e2e: 真浏览器加载驾驶舱, bump ui epoch, 验证页面真的自刷新。
// 对应链路: omni dashboard ui-reload → POST /api/dev/bump → 前端 devReload 3s 轮询 → location.reload()。
// 注意: devReload 只在 build 产物模式生效 (vite dev 下为 no-op), 所以本 spec 需要打后端直出端口
// (OMNI_E2E_DASHBOARD_PORT, 不设 OMNI_E2E_FRONTEND_PORT)。

import { test, expect } from '@playwright/test'

test('ui bump 触发页面自刷新 (不动 VSCode / 不重启进程)', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('#root')).not.toBeEmpty()
  // 等 devReload 首次 tick 记下基线 token (加载时立即 tick 一次, 1s 余量足够)
  await page.waitForTimeout(1000)

  // 页面上放个标记; 真刷新会把它清掉 — 这是"页面确实重载了"的客观证据
  await page.evaluate(() => { (window as unknown as Record<string, unknown>).__devReloadMarker = 'before-bump' })

  const res = await page.request.post('/api/dev/bump', { data: { target: 'ui' } })
  expect(res.ok()).toBeTruthy()

  // 3s 轮询周期内应触发 reload; 给 12s 余量。导航瞬间 evaluate 会抛, 视同标记已消失。
  await expect.poll(
    () => page.evaluate(() => (window as unknown as Record<string, unknown>).__devReloadMarker ?? null).catch(() => null),
    { timeout: 12_000, intervals: [500] },
  ).toBeNull()

  // 刷新后的页面要能正常渲染, 不是白屏
  await expect(page.locator('#root')).not.toBeEmpty()
})
