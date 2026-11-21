import { test, expect } from '@playwright/test'
import { SHOTS, setupErrorLogging } from './helpers'

test.beforeEach(async ({ page }) => {
  setupErrorLogging(page)
  await page.goto('/')
  await page.waitForTimeout(1500)
})

test('F1 Ctrl+K 触发命令面板', async ({ page }) => {
  await page.keyboard.press('Control+k')
  await expect(page.getByRole('combobox')).toBeVisible({ timeout: 5000 })
})

test('F2 输入 worker 名 — 出 worker section + 候选', async ({ page }) => {
  await page.keyboard.press('Control+k')
  const search = page.getByRole('combobox')
  await search.fill('block_designer')
  await page.waitForTimeout(500)
  await expect(page.getByText('block_designer').first()).toBeVisible()
})

test('F3 输入 PROGRESS — 命中 note', async ({ page }) => {
  await page.keyboard.press('Control+k')
  await page.getByRole('combobox').fill('PROGRESS')
  await page.waitForTimeout(500)
  await expect(page.getByText('PROGRESS').first()).toBeVisible()
})

test('F4 section 分组 (WORKER / NOTE / PM 总览)', async ({ page }) => {
  await page.keyboard.press('Control+k')
  await page.waitForTimeout(500)
  await expect(page.getByText('WORKER').first()).toBeVisible({ timeout: 5000 })
  await page.screenshot({ path: `${SHOTS}/cmdk_sections.png`, fullPage: true })
})

test('F5 Enter 选中项目开 tab', async ({ page }) => {
  await page.keyboard.press('Control+k')
  const search = page.getByRole('combobox')
  await search.fill('README')
  await page.waitForTimeout(800)
  await page.keyboard.press('Enter')
  await page.waitForTimeout(500)
  await expect(page.locator('text=docs/README.md')).toBeVisible({ timeout: 10000 })
})

test('F6 Esc 关闭命令面板', async ({ page }) => {
  await page.keyboard.press('Control+k')
  const search = page.getByRole('combobox')
  await expect(search).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(search).not.toBeVisible()
})
