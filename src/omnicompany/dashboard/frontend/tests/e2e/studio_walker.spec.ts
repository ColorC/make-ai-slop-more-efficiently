// 统一设计工作室二期 e2e(验收项目=walker, 真 UI 路径)。
// 覆盖 plan §8.1 场景一/二/四/六/七: 打开 walker 项目页 → 默认 tab=材料轨迹 → 画布可见有节点
// → 点设计评审 v2 节点 → 详情栏出现 → 写一条版本级评论提交 → 刷新后评论仍在
// → 点发起下一步 → 剪贴板含上下文包 → 决策树 tab 已删除不复存在(DEC-2026-07-04-240)。
//
// 数据前提: 注册表里有 walker 项目, 且审阅台 store 有 walker 的 3 轨/版本链材料(真实验收数据)。
// 后端服起停由 global-setup 管; 本 spec 不要求在本次跑通(起不来如实报)。

import { test, expect, type Page } from '@playwright/test'
import { setupErrorLogging } from './helpers'

test.use({ permissions: ['clipboard-read', 'clipboard-write'] })

test.beforeEach(async ({ page }) => {
  setupErrorLogging(page)
})

async function openWalkerProject(page: Page): Promise<void> {
  await page.goto('/')
  const board = page.getByTestId('project-board')
  await expect(board).toBeVisible({ timeout: 15_000 })
  const walkerCard = page.locator('[data-omni-uri="omni://project/walker"]').first()
  await expect(walkerCard).toBeVisible({ timeout: 15_000 })
  await walkerCard.click()
  // 打开的是 dockview 内 project 页签, 惰性面板 + 项目 resolver 冷启动要读外部盘 index(慢), 放宽等待。
  await expect(page.getByTestId('project-detail')).toBeVisible({ timeout: 20_000 })
}

test('walker 材料轨迹: 默认画布 → 点版本节点 → 写评论持久 → 发起下一步 → 无决策树入口', async ({ page }) => {
  await openWalkerProject(page)

  // 场景一: 默认进入"材料轨迹"tab, 画布可见
  await expect(page.getByTestId('project-tab-canvas')).toBeVisible()
  const canvas = page.getByTestId('review-canvas')
  await expect(canvas).toBeVisible({ timeout: 15_000 })
  // A10: 视图标题是"材料轨迹", 不是"决策树"
  await expect(canvas).toContainText('材料轨迹')

  // 场景二: 画布上有版本节点(冷启动读外部盘略慢, 放宽)
  const anyNode = page.locator('[data-testid^="canvas-node-"]')
  await expect(anyNode.first()).toBeVisible({ timeout: 15_000 })

  // 定位"设计评审 v2"节点: 从 /review-canvas 投影取该 family 的最高版本 id, 用真实 id 点击。
  const canvasData = await page.evaluate(async () => {
    const r = await fetch('/api/boss-sight/reviewstage/review-canvas?project=walker')
    return r.ok ? r.json() : null
  })
  expect(canvasData, 'review-canvas 投影应可用').not.toBeNull()
  const reviewTrack = (canvasData.tracks || []).find((t: { track: string }) => t.track === '设计评审')
  test.skip(!reviewTrack, 'walker 暂无"设计评审"轨, 跳过版本级子场景(数据前提未满足)')
  const fam = reviewTrack.families[0]
  const v2 = [...fam.materials].sort((a: { version: number }, b: { version: number }) => (b.version ?? 0) - (a.version ?? 0))[0]

  // 场景四: 点节点 → 详情栏出现
  await page.locator(`[data-testid="canvas-node-${v2.id}"]`).click()
  const detail = page.getByTestId('canvas-detail')
  await expect(detail).toBeVisible()
  await expect(detail).toContainText(String(v2.title).slice(0, 8))

  // 场景四: 写一条版本级评论提交, 刷新后仍在(评论真源=authored Note material_version)
  const stamp = `e2e画布意见-${Date.now()}`
  await page.getByTestId('canvas-comment-input').fill(stamp)
  await page.getByTestId('canvas-comment-submit').click()
  // 提交后画布重载, 详情栏关闭; 重新点节点看评论是否水合回来
  await expect(async () => {
    await page.locator(`[data-testid="canvas-node-${v2.id}"]`).click({ trial: false })
    await expect(page.getByTestId('canvas-detail')).toContainText(stamp, { timeout: 3000 })
  }).toPass({ timeout: 20_000 })

  // 硬刷新页面再验证一次持久性(不是仅本地态)
  await page.reload()
  await expect(page.getByTestId('review-canvas')).toBeVisible({ timeout: 15_000 })
  await page.locator(`[data-testid="canvas-node-${v2.id}"]`).click()
  await expect(page.getByTestId('canvas-detail')).toContainText(stamp, { timeout: 8000 })

  // 场景六: 发起下一步 → 剪贴板含上下文包(材料标题 + 项目)
  await page.getByTestId('canvas-next-step').click()
  await expect(page.getByTestId('canvas-next-step')).toContainText('已复制', { timeout: 4000 })
  const clip = await page.evaluate(() => navigator.clipboard.readText())
  expect(clip).toContain('walker')
  expect(clip).toContain(String(v2.title).slice(0, 8))
  expect(clip).toContain('发起下一步工作包')

  // 决策树标签页已删除(DEC-2026-07-04-240:裸 DAG 外观封禁)——项目页不应再有该入口
  await expect(page.getByTestId('project-tab-tree')).toHaveCount(0)
})
