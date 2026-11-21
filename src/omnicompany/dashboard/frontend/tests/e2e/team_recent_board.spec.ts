// 「最近 team」看板 e2e — 真 UI 路径: 顶栏「更多」菜单打开看板 → 按最近排序列出 team →
// 复制 team id → 点开进既有团队拓扑视图(结构图 + ReactFlow)。复活被赶进角落的 team 设施。
import { test, expect } from '@playwright/test'

test.use({ permissions: ['clipboard-read', 'clipboard-write'] })

test('最近 team 看板: 更多菜单打开 → 列出 team → 复制 id → 点开拓扑', async ({ page }) => {
  await page.goto('/')
  // 顶栏「…更多」→「最近 team」
  await page.getByTestId('cockpit-more').click()
  await page.getByTestId('cockpit-more-team-board').click()

  const board = page.getByTestId('team-recent-board')
  await expect(board).toBeVisible()
  const rows = page.getByTestId('team-recent-row')
  await expect(rows.first()).toBeVisible({ timeout: 15_000 })
  expect(await rows.count()).toBeGreaterThan(0)

  // 行内 kebab 复制 team id
  const kebab = page.getByTestId('team-recent-kebab').first()
  await kebab.click()
  await page.getByTestId('team-kebab-copy-id').first().click()
  const clip = await page.evaluate(() => navigator.clipboard.readText())
  expect(clip.length).toBeGreaterThan(0)

  // 点开第一个 team → 团队视图(结构图 tab + ReactFlow 拓扑画布)
  await rows.first().click()
  await expect(page.locator('.react-flow').first()).toBeVisible({ timeout: 15_000 })
  await expect(page.getByText('结构图').first()).toBeVisible()
})
