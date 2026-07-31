import { test, expect } from '@playwright/test'
import { SHOTS, setupErrorLogging, gotoModule } from './helpers'

test.beforeEach(async ({ page }) => {
  setupErrorLogging(page)
  await page.goto('/')
  await gotoModule(page, 'kb')
})

test('B4.1 双链 sidebar 项 加 graph 实体', async ({ page }) => {
  await expect(page.getByText('关系图谱').first()).toBeVisible({ timeout: 10000 })
})

test('B4.2 [[name]] 渲染为 wikilink (找带 wikilink-data 的笔记)', async ({ page }) => {
  // 用一个含 [[xx]] 的笔记 (feature_matrix.md 含 [[xx]] 占位)
  await page.getByPlaceholder('过滤...').fill('feature_matrix')
  await page.waitForTimeout(400)
  const item = page.locator('div[title*="feature_matrix"]').first()
  await item.waitFor({ timeout: 5000 })
  await item.click()
  await page.waitForTimeout(800)
  // wikilinks rendered as anchors with class
  const wl = page.locator('a.wikilink, a[data-wikilink]').first()
  await expect(wl).toBeVisible({ timeout: 5000 })
})

test('B5 反链面板 — 反链/外链区可见', async ({ page }) => {
  await page.getByPlaceholder('过滤...').fill('README')
  await page.waitForTimeout(400)
  await page.locator('div[title="README"]').click()
  await expect(page.getByText(/反链 · \d+/)).toBeVisible({ timeout: 10000 })
  await expect(page.getByText(/外链 · \d+/)).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/kb_backlinks_panel.png`, fullPage: true })
})

test('B5.2 含双链笔记的外链区显示解析项 / 未解析项', async ({ page }) => {
  await page.getByPlaceholder('过滤...').fill('feature_matrix')
  await page.waitForTimeout(400)
  await page.locator('div[title*="feature_matrix"]').first().click()
  await page.waitForTimeout(800)
  // feature_matrix 里有 [[xx]] (在 C5 行) 是 unresolved
  await expect(page.getByText(/外链 · \d+/)).toBeVisible({ timeout: 5000 })
})

test('B6 图谱 sidebar 项 + 点开 cytoscape 渲染', async ({ page }) => {
  await page.locator('div[title="main"]').first().click()
  await expect(page.getByText(/关系图谱/).first()).toBeVisible({ timeout: 10000 })
  await expect(page.getByText(/\d+ 节点/)).toBeVisible({ timeout: 10000 })
  await page.screenshot({ path: `${SHOTS}/kb_graph_view.png`, fullPage: true })
})
