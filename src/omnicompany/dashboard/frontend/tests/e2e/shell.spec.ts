import { test, expect } from '@playwright/test'
import { SHOTS, setupErrorLogging, gotoModule } from './helpers'

test.beforeEach(async ({ page }) => {
  setupErrorLogging(page)
  await page.goto('/')
})

test('A1 ActivityBar 4 项可见', async ({ page }) => {
  await expect(page.locator('button[title="知识库"]')).toBeVisible()
  await expect(page.locator('button[title="项目"]')).toBeVisible()
  await expect(page.locator('button[title="系统"]')).toBeVisible()
  await expect(page.locator('button[title="设置"]')).toBeVisible()
})

test('A2 切 module sidebar 跟随', async ({ page }) => {
  await gotoModule(page, 'kb')
  await expect(page.locator('text=知识库').first()).toBeVisible()
  await gotoModule(page, 'pm')
  await expect(page.locator('text=项目').first()).toBeVisible()
  await gotoModule(page, 'system')
  await expect(page.locator('text=系统').first()).toBeVisible()
  await gotoModule(page, 'settings')
  await expect(page.locator('text=设置').first()).toBeVisible()
})

test('A3 Sidebar 标题 + 过滤框可见', async ({ page }) => {
  await expect(page.getByPlaceholder('过滤...')).toBeVisible()
})

test('A4 实体分组 (system 默认 = Worker / Team / Material; Agent 会话独立模块)', async ({ page }) => {
  // 默认 system 模块, 显 Worker 等
  await expect(page.getByText('Worker', { exact: true })).toBeVisible({ timeout: 10000 })
  // Agent 会话已迁出 system → 切到 agent 模块才看见
  const { gotoModule } = await import('./helpers')
  await gotoModule(page, 'agent')
  await page.waitForTimeout(300)
  await expect(page.getByText(/Agent 会话 · \d+/)).toBeVisible()
})

test('A6 EditorArea 默认显 Welcome', async ({ page }) => {
  await expect(page.getByText('omnicompany · web 端')).toBeVisible()
  await expect(page.getByText('从左侧 Activity Bar')).toBeVisible()
})

test('A8 BottomPanel 默认开 + 标签可见', async ({ page }) => {
  await expect(page.getByRole('button', { name: '事件流' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Trace 列表' })).toBeVisible()
})

test('A9 BottomPanel 切换标签', async ({ page }) => {
  await page.getByRole('button', { name: 'Trace 列表' }).click()
  await expect(page.getByPlaceholder(/过滤 task_desc/)).toBeVisible()
  await page.getByRole('button', { name: '事件流' }).click()
  await expect(page.getByText(/来源 SQLiteBus/)).toBeVisible()
})

test('A10 StatusBar 显 0 标签 + 快捷键提示', async ({ page }) => {
  await expect(page.getByText('0 标签')).toBeVisible()
  await expect(page.getByText(/跨实体跳转/)).toBeVisible()
})

test('A11 Welcome 4 模块卡片可见', async ({ page }) => {
  await expect(page.getByText('知识库').nth(0)).toBeVisible()
  await expect(page.getByText('PM 总览 + Goals').or(page.getByText(/PM 总览/))).toBeVisible()
})

test('A12 Modal Esc 关闭 + 取消按钮关闭', async ({ page }) => {
  // session entity 现在在 'agent' 模块 (S12)
  const { gotoModule } = await import('./helpers')
  await gotoModule(page, 'agent')
  await page.waitForTimeout(300)
  await page.locator('button[title="新建 agent 会话"]').click()
  const ta = page.locator('textarea[placeholder*="首条消息"]')
  await expect(ta).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(ta).not.toBeVisible()

  await page.locator('button[title="新建 agent 会话"]').click()
  await expect(ta).toBeVisible()
  await page.getByRole('button', { name: '取消' }).click()
  await expect(ta).not.toBeVisible()
})

test('A_screenshot 取首页截图存档', async ({ page }) => {
  await page.screenshot({ path: `${SHOTS}/shell_first_visit.png`, fullPage: true })
})
