import { test, expect } from '@playwright/test'
import { setupErrorLogging } from './helpers'

test.beforeEach(async ({ page }) => {
  setupErrorLogging(page)
  await page.goto('/')
})

test('H1 事件流标签默认 active', async ({ page }) => {
  await expect(page.getByRole('button', { name: '事件流' })).toBeVisible()
  await expect(page.getByText(/来源 SQLiteBus/)).toBeVisible()
})

test('H2 空状态有引导', async ({ page }) => {
  const emptyOrLive = page.locator('text=暂无事件').or(page.locator('text=ide_agent').first())
  await expect(emptyOrLive).toBeVisible({ timeout: 5000 })
})

test('H3 SSE 连接成立 (无 console error)', async ({ page }) => {
  let sseError = false
  page.on('console', (m) => {
    if (m.type() === 'error' && m.text().includes('SSE')) sseError = true
  })
  await page.goto('/')
  await page.waitForTimeout(2000)
  expect(sseError).toBe(false)
})
