import { test, expect } from '@playwright/test'
import { SHOTS, setupErrorLogging, gotoModule } from './helpers'

// 给 KB 用一个含 GFM 表格的笔记测试. 选 feature_matrix.md (我自己写的, 含表格)
const TEST_NOTE = 'plans/dashboard/[2026-05-01]WEB-FOUNDATION/feature_matrix'

test.beforeEach(async ({ page }) => {
  setupErrorLogging(page)
  await page.goto('/')
  await gotoModule(page, 'kb')
})

test('M1 GFM 表格 — feature_matrix 笔记的表格被渲染为 <table>', async ({ page }) => {
  await page.getByPlaceholder('过滤...').fill('feature_matrix')
  await page.waitForTimeout(400)
  await page.locator(`div[title="${TEST_NOTE}"]`).first().click()
  await page.waitForTimeout(800)
  // 等 markdown 渲染完
  await expect(page.locator('.markdown-body table').first()).toBeVisible({ timeout: 10000 })
  // 表头 (th)
  await expect(page.locator('.markdown-body table th').first()).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/kb_gfm_table.png`, fullPage: true })
})

test('M2 笔记 sidebar 是树形 (不是 flat list)', async ({ page }) => {
  // 顶层目录在 sidebar 用 div[title="dirpath"], plans / standards 等
  await expect(page.locator('div[title="plans"]')).toBeVisible({ timeout: 10000 })
  await expect(page.locator('div[title="standards"]')).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/kb_note_tree.png`, fullPage: true })
})

test('M3 笔记树 顶层目录默认展开', async ({ page }) => {
  // plans 顶层默认展开 → 能看到 [date]TOPIC 子目录
  await expect(page.locator('div[title^="plans/"]').first()).toBeVisible({ timeout: 10000 })
})
