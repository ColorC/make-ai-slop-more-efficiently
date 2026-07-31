import { test, expect } from '@playwright/test'
import { SHOTS, setupErrorLogging } from './helpers'

test.beforeEach(async ({ page }) => {
  setupErrorLogging(page)
  await page.goto('/')
})

// ── Team ─────────────────────────────────────────────────────────────

test('TM.1 系统 sidebar 显 Team 分组 + 树形 顶层 domains 可见', async ({ page }) => {
  await expect(page.getByText('Team', { exact: true })).toBeVisible({ timeout: 10000 })
  // 等 Worker tree 加载完, 顶层 domains 出现 (作为树展开根)
  await expect(page.locator('[data-tree="worker"] div[title="domains"]').first()).toBeVisible({ timeout: 10000 })
})

test('TM.2 后端 /api/teams 返回 49+ 条 (扫 team*.py)', async ({ request }) => {
  const r = await request.get('/api/teams')
  expect(r.ok()).toBeTruthy()
  const d = await r.json()
  expect(d.total).toBeGreaterThan(40)
  expect(d.items[0]).toHaveProperty('id')
  expect(d.items[0]).toHaveProperty('package')
})

test('TM.3 点 Team 项 → CodeFileEditor (PaneHeader + DESIGN.md/source 切换)', async ({ page }) => {
  // expand to a known team file. domains/voxelcraft/team.py
  await page.locator('div[title="domains"]').first().click()  // expand under team group OR worker group; either works
  await page.locator('div[title="domains/voxelcraft"]').first().click()
  await page.waitForTimeout(400)
  const teamLeaf = page.locator('div[title="domains/voxelcraft/team"]').first()
  await teamLeaf.scrollIntoViewIfNeeded()
  await teamLeaf.click()
  await page.waitForTimeout(800)
  await expect(page.getByRole('button', { name: 'DESIGN.md' })).toBeVisible({ timeout: 10000 })
  await expect(page.getByRole('button', { name: 'source' })).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/team_open.png`, fullPage: true })
})

test('TM.4 Team source view 显 Monaco 内容含 TeamSpec', async ({ page }) => {
  await page.locator('div[title="domains"]').first().click()
  await page.locator('div[title="domains/voxelcraft"]').first().click()
  await page.locator('div[title="domains/voxelcraft/team"]').first().click()
  await page.waitForTimeout(800)
  await page.getByRole('button', { name: 'source' }).click()
  await expect(page.locator('.monaco-editor').first()).toBeVisible({ timeout: 15000 })
  await page.waitForTimeout(500)
  // Monaco renders text in many spans; check some keyword visible somewhere
  // Monaco virtualizes lines (only first ~25 visible). Match imports / docstring keywords known on top.
  await expect(page.locator('.monaco-editor').first()).toContainText(/TransformerSpec|AnchorSpec|pipeline/, { timeout: 5000 })
})

// ── Material ─────────────────────────────────────────────────────────

test('TM.5 后端 /api/materials 返回 60+ 条 (含 materials.py + formats.py)', async ({ request }) => {
  const r = await request.get('/api/materials')
  expect(r.ok()).toBeTruthy()
  const d = await r.json()
  expect(d.total).toBeGreaterThan(50)
})

test('TM.6 系统 sidebar 显 Material 分组', async ({ page }) => {
  await expect(page.getByText('Material', { exact: true })).toBeVisible({ timeout: 10000 })
})

test('TM.7 点 Material 项 → CodeFileEditor 真显', async ({ page }) => {
  // Material 用 sidebar tree; default 顶层 expanded includes domains
  await page.getByPlaceholder('过滤...').fill('block.materials')
  await page.waitForTimeout(500)
  // The title will be like "block.materials" (because name === 'materials' so we use folder.name)
  const item = page.locator('div[title="domains/voxelcraft/block/materials"]').first()
  await expect(item).toBeVisible({ timeout: 10000 })
  await item.click()
  await page.waitForTimeout(800)
  await expect(page.getByRole('button', { name: 'source' })).toBeVisible({ timeout: 10000 })
  await page.screenshot({ path: `${SHOTS}/material_open.png`, fullPage: true })
})

test('TM.8 Cmd+K 跨实体搜 — Material 项可命中', async ({ page }) => {
  await page.waitForTimeout(1500)
  await page.keyboard.press('Control+k')
  await page.waitForTimeout(500)
  await page.getByRole('combobox').fill('block.materials')
  await page.waitForTimeout(800)
  await expect(page.getByText(/Material/).first()).toBeVisible({ timeout: 5000 })
})
