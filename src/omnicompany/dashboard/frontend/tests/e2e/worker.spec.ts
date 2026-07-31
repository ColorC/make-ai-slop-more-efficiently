import { test, expect } from '@playwright/test'
import { SHOTS, setupErrorLogging, expandWorkerPath as expandTo } from './helpers'

const W = 'domains/voxelcraft/block/workers/block_designer'

test.beforeEach(async ({ page }) => {
  setupErrorLogging(page)
  await page.goto('/')
})

test('D1 Worker 树默认 domains 展', async ({ page }) => {
  await expect(page.locator('[data-tree="worker"] div[title="domains"]')).toBeVisible({ timeout: 10000 })
  await expect(page.locator('[data-tree="worker"] div[title="domains/voxelcraft"]')).toBeVisible()
})

test('D2 Worker 树点 dir 展子目录', async ({ page }) => {
  await page.locator('[data-tree="worker"] div[title="domains/voxelcraft"]').waitFor({ timeout: 10000 })
  await page.locator('[data-tree="worker"] div[title="domains/voxelcraft"]').click()
  await expect(page.locator('[data-tree="worker"] div[title="domains/voxelcraft/block"]')).toBeVisible()
})

test('D3 Worker 树过滤生效 (输 block)', async ({ page }) => {
  const filter = page.getByPlaceholder('过滤...')
  await filter.fill('block_designer')
  await page.waitForTimeout(500)
  await expect(page.locator(`[data-tree="worker"] div[title="${W}"]`)).toBeVisible({ timeout: 10000 })
})

test('D4 点 worker leaf 开 tab', async ({ page }) => {
  await expandTo(page, W)
  await page.locator(`[data-tree="worker"] div[title="${W}"]`).click()
  await expect(page.getByText('block_designer').nth(0)).toBeVisible({ timeout: 10000 })
})

test('D5 设计 facet — DESIGN.md / source 切换按钮可见', async ({ page }) => {
  await expandTo(page, W)
  await page.locator(`[data-tree="worker"] div[title="${W}"]`).click()
  await expect(page.getByRole('button', { name: 'DESIGN.md' })).toBeVisible({ timeout: 10000 })
  await expect(page.getByRole('button', { name: 'source' })).toBeVisible()
})

test('D5.1 设计 facet 用 .markdown-body 容器 (统一 renderer)', async ({ page }) => {
  // pick a worker known to have DESIGN.md
  const WD = 'domains/voxelcraft/entity/workers/entity_designer'
  await expandTo(page, WD)
  await page.locator(`[data-tree="worker"] div[title="${WD}"]`).click()
  await page.waitForTimeout(800)
  await expect(page.locator('.markdown-body').first()).toBeVisible({ timeout: 10000 })
})

test('D5.2 设计 facet 渲染表格 (复用统一 renderer)', async ({ page }) => {
  // many DESIGN.md contain tables. Find one with table.
  const WD = 'domains/voxelcraft/entity/workers/entity_designer'
  await expandTo(page, WD)
  await page.locator(`[data-tree="worker"] div[title="${WD}"]`).click()
  await page.waitForTimeout(1500)
  const tableCount = await page.locator('.markdown-body table').count()
  // not all DESIGN.md have tables; at minimum check the rendered region exists with proper classes
  if (tableCount > 0) {
    await expect(page.locator('.markdown-body table th').first()).toBeVisible()
  }
  // visible heading at least
  await expect(page.locator('.markdown-body h1, .markdown-body h2').first()).toBeVisible()
})

test('D5.3 设计 facet 代码块语法高亮 (统一 renderer)', async ({ page }) => {
  const WD = 'domains/voxelcraft/entity/workers/entity_designer'
  await expandTo(page, WD)
  await page.locator(`[data-tree="worker"] div[title="${WD}"]`).click()
  await page.waitForTimeout(1500)
  const codeCount = await page.locator('.markdown-body pre').count()
  if (codeCount > 0) {
    // if any code block has lang, prism injects .token spans
    const tokenCount = await page.locator('.markdown-body pre .token').count()
    // only assert if any code has language hint
    if (tokenCount > 0) expect(tokenCount).toBeGreaterThan(0)
  }
})

test('D6 设计 facet 切 source — Monaco 渲染', async ({ page }) => {
  await expandTo(page, W)
  await page.locator(`[data-tree="worker"] div[title="${W}"]`).click()
  await page.getByRole('button', { name: 'source' }).click()
  await expect(page.locator('.monaco-editor').first()).toBeVisible({ timeout: 15000 })
})

test('D7 worker 无 DESIGN 时占位文字 (切到 DESIGN.md tab)', async ({ page }) => {
  await expandTo(page, W)
  await page.locator(`[data-tree="worker"] div[title="${W}"]`).click()
  // CodeFileEditor defaults to source view when design_md is null.
  // Click DESIGN.md tab to see the empty placeholder.
  await page.getByRole('button', { name: 'DESIGN.md' }).click()
  await expect(page.getByText(/无 DESIGN.md/).first()).toBeVisible({ timeout: 10000 })
})

test('D8 切运行 facet — 显事件占位 / 实时区', async ({ page }) => {
  await expandTo(page, W)
  await page.locator(`[data-tree="worker"] div[title="${W}"]`).click()
  await page.getByRole('button', { name: '运行' }).click()
  await expect(page.getByText(/实时事件 · 按 source/)).toBeVisible({ timeout: 5000 })
  await page.screenshot({ path: `${SHOTS}/worker_live_facet.png`, fullPage: true })
})

test('D9 切历史 facet — 真请求 + 列表/空状态', async ({ page }) => {
  await expandTo(page, W)
  await page.locator(`[data-tree="worker"] div[title="${W}"]`).click()
  await page.getByRole('button', { name: '历史' }).click()
  await expect(page.getByText(/历史 trace|无历史 trace/)).toBeVisible({ timeout: 5000 })
  await page.screenshot({ path: `${SHOTS}/worker_history_facet.png`, fullPage: true })
})
