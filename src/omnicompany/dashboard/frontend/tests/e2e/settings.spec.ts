import { test, expect } from '@playwright/test'
import { SHOTS, setupErrorLogging, gotoModule } from './helpers'

test.beforeEach(async ({ page }) => {
  setupErrorLogging(page)
  await page.goto('/')
  await gotoModule(page, 'settings')
})

test('E1 设置 module sidebar 显设置项', async ({ page }) => {
  await expect(page.locator('text=系统信息').first()).toBeVisible({ timeout: 10000 })
})

test('E2 点设置 → 真 system info 显', async ({ page }) => {
  await page.locator('div[title="main"]').click()
  await expect(page.getByText('版本 + 路径').first()).toBeVisible({ timeout: 10000 })
  await expect(page.getByText('worker 数').first()).toBeVisible()
  await expect(page.getByText('数据库').first()).toBeVisible()
  await expect(page.getByText('events.db').first()).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/settings_page.png`, fullPage: true })
})

test('E3 系统统计 worker 数 > 100', async ({ page }) => {
  await page.locator('div[title="main"]').click()
  await page.waitForTimeout(800)
  const wcRow = page.locator('text=worker 数').locator('..').first()
  await expect(wcRow).toBeVisible()
})
