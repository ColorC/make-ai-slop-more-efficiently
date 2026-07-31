import { test, expect } from '@playwright/test'
import { SHOTS, setupErrorLogging, gotoModule } from './helpers'

test.beforeEach(async ({ page }) => {
  setupErrorLogging(page)
  await page.goto('/')
  // session entity moved to dedicated 'agent' module (S12, 2026-05-02)
  await gotoModule(page, 'agent')
  await page.waitForTimeout(300)
})

test('D10 Agent 会话组 + 按钮可见', async ({ page }) => {
  await expect(page.locator('button[title="新建 agent 会话"]')).toBeVisible({ timeout: 10000 })
  await expect(page.getByText(/Agent 会话 · \d+/)).toBeVisible()
})

test('D11.a 点 + 弹 modal', async ({ page }) => {
  await page.locator('button[title="新建 agent 会话"]').click()
  await expect(page.locator('textarea[placeholder*="首条消息"]')).toBeVisible()
  await expect(page.getByText('Ctrl/Cmd + Enter 提交')).toBeVisible()
})

test('D11.b modal Esc 关闭', async ({ page }) => {
  await page.locator('button[title="新建 agent 会话"]').click()
  const ta = page.locator('textarea[placeholder*="首条消息"]')
  await expect(ta).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(ta).not.toBeVisible()
})

test('D11.c modal 取消按钮关闭', async ({ page }) => {
  await page.locator('button[title="新建 agent 会话"]').click()
  await page.getByRole('button', { name: '取消' }).click()
  await expect(page.locator('textarea[placeholder*="首条消息"]')).not.toBeVisible()
})

test('D11.d modal 输入空时创建按钮无效 (验空字符串)', async ({ page }) => {
  await page.locator('button[title="新建 agent 会话"]').click()
  await page.getByRole('button', { name: '创建' }).click()
  await page.waitForTimeout(300)
  await expect(page.locator('textarea[placeholder*="首条消息"]')).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/session_modal_empty.png`, fullPage: true })
})
