import { test, expect } from '@playwright/test'
import { SHOTS, setupErrorLogging, gotoModule } from './helpers'

test.beforeEach(async ({ page }) => {
  setupErrorLogging(page)
  await page.goto('/')
  await gotoModule(page, 'kb')
})

test('B1 KB sidebar 显 note 列表 (真后端 /api/notes, 树形)', async ({ page }) => {
  await expect(page.getByText('Note', { exact: true })).toBeVisible({ timeout: 10000 })
  // 根级文件 PROGRESS / README 在树根 (parts.length=1)
  await expect(page.locator('div[title="PROGRESS"]')).toBeVisible({ timeout: 10000 })
  await expect(page.locator('div[title="README"]')).toBeVisible()
})

test('B2 点 note 开 tab', async ({ page }) => {
  const item = page.locator('div[title="README"]').first()
  await item.waitFor({ timeout: 10000 })
  await item.click()
  await expect(page.locator('text=docs/README.md')).toBeVisible({ timeout: 10000 })
})

test('B3 Note tab 真 markdown 渲染 (h1 / 段落)', async ({ page }) => {
  const item = page.locator('div[title="README"]').first()
  await item.waitFor({ timeout: 10000 })
  await item.click()
  await expect(page.locator('.markdown-body h1, .markdown-body p').first()).toBeVisible({ timeout: 10000 })
  await page.screenshot({ path: `${SHOTS}/kb_note_render.png`, fullPage: true })
})

test('B_filter Note 过滤生效', async ({ page }) => {
  await page.getByPlaceholder('过滤...').fill('PROGRESS')
  await page.waitForTimeout(500)
  await expect(page.locator('div[title="PROGRESS"]')).toBeVisible()
})
