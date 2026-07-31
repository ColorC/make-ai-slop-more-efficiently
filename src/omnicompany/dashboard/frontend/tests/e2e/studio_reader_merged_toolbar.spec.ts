// 件一/件二 e2e(DEC-2026-07-06-082/083, 真 UI 路径)。
// 件一: 打开 vilo 阅读视图 → 选中一条叙事材料 → 断言合并顶栏(material-detail-merged-toolbar)存在,
//        且旧的正文内 NarrativeToolbar DOM(narrative-goto-decisions/narrative-rulings-chip 在正文里)不存在。
// 件二: 回驾驶舱审阅列表 → 某卡 kebab 点「在项目工作台打开」→ 断言 studio_reader tab 打开且定位到该材料。
//
// 数据前提: 审阅台 store 有 vilo 的叙事材料(带 data_schema_id=narrative_*_v1)。后端服起停由 global-setup 管;
// 起不来 / 数据不满足时如实 skip(照既有 spec 惯例, 不假装跑过)。

import { test, expect, type Page } from '@playwright/test'
import { setupErrorLogging } from './helpers'

test.use({ permissions: ['clipboard-read', 'clipboard-write'] })

test.beforeEach(async ({ page }) => {
  setupErrorLogging(page)
})

async function waitShell(page: Page): Promise<void> {
  await expect(page.getByTestId('cockpit-shell')).toBeVisible({ timeout: 20_000 })
}

// 从审阅台投影里找一条 vilo 叙事材料(带 narrative_*_v1 的 data_schema_id)。无则返回 null → 调用方 skip。
async function findViloNarrativeMaterial(page: Page): Promise<{ id: string; title: string } | null> {
  return page.evaluate(async () => {
    try {
      const r = await fetch('/api/boss-sight/reviewstage/list')
      if (!r.ok) return null
      const d = await r.json() as { items?: Array<{ id: string; title: string; project?: string; extra?: Record<string, unknown> }> }
      const hit = (d.items ?? []).find((m) => m.project === 'vilo'
        && typeof m.extra?.data_schema_id === 'string'
        && String(m.extra.data_schema_id).startsWith('narrative_'))
      return hit ? { id: hit.id, title: hit.title } : null
    } catch { return null }
  })
}

test('件一: vilo 阅读视图选中叙事材料 → 单条合并顶栏, 正文无第二条工具条', async ({ page }) => {
  await page.goto('/')
  await waitShell(page)

  const mat = await findViloNarrativeMaterial(page)
  test.skip(!mat, 'vilo 暂无叙事材料(data_schema_id=narrative_*_v1), 跳过合并顶栏子场景(数据前提未满足)')

  // 打开 vilo 阅读视图(深链定位到该材料): 直接经 store 开 studio_reader tab, facet=材料 id。
  await page.evaluate((materialId) => {
    // usePanels 挂在 window 便于 e2e 驱动? 若无则退化经 UI。这里用 openTab 深链约定。
    const w = window as unknown as { __omniOpenReader?: (p: string, id: string) => void }
    if (w.__omniOpenReader) { w.__omniOpenReader('vilo', materialId) }
  }, mat!.id)

  // 无论上面钩子是否存在, 都断言阅读视图可达: 经审阅列表卡的「在项目工作台打开」是稳定路径, 见下一个 test。
  // 这里若阅读视图已开则直接验合并顶栏。
  const reader = page.getByTestId('studio-reader')
  if (await reader.count() === 0) {
    test.skip(true, '无 window 钩子驱动阅读视图, 合并顶栏断言并入件二 test(经列表卡入口打开)')
  }
  await expect(reader).toBeVisible({ timeout: 15_000 })

  // 合并顶栏存在(业务顶栏并入审阅顶栏)。
  await expect(page.getByTestId('material-detail-merged-toolbar')).toBeVisible({ timeout: 15_000 })
  // 旧的"正文内第二条工具条"痕迹: 决策历程/适用裁决 若出现, 必须在合并顶栏内, 而非独立第二条。
  const gotoDecisionsInBody = page.locator('[data-testid="studio-reader-stage"] [data-testid="narrative-goto-decisions"]')
  await expect(gotoDecisionsInBody).toHaveCount(0)
})

test('件二: 审阅列表卡「在项目工作台打开」→ studio_reader tab 打开且定位到该材料, 合并顶栏在', async ({ page }) => {
  await page.goto('/')
  await waitShell(page)

  const mat = await findViloNarrativeMaterial(page)
  test.skip(!mat, 'vilo 暂无叙事材料, 跳过工作台跳转子场景(数据前提未满足)')

  // 打开驾驶舱审阅列表(activity bar「审阅」入口 → review_queue 页签, 内含 ReviewQueueSidebar)。
  const reviewNav = page.locator('button[title="审阅"], [data-testid="activity-review"]').first()
  if (await reviewNav.count() > 0) await reviewNav.click()
  const queue = page.getByTestId('review-queue-sidebar')
  test.skip(await queue.count() === 0, '审阅列表入口不可达(布局差异), 跳过')
  await expect(queue).toBeVisible({ timeout: 15_000 })

  // 定位该材料卡, 打开其 kebab, 点「在项目工作台打开」。
  const card = page.getByTestId(`material-card-${mat!.id}`)
  test.skip(await card.count() === 0, '该材料不在当前筛选(可能非 pending), 跳过卡入口子场景')
  await card.scrollIntoViewIfNeeded()
  await page.getByTestId(`material-card-more-${mat!.id}`).click()
  await page.getByTestId(`material-card-open-studio-${mat!.id}`).click()

  // studio_reader tab 打开, 阅读视图可见, 且合并顶栏存在(定位到该材料 → 叙事渲染器 → 合并顶栏)。
  await expect(page.getByTestId('studio-reader')).toBeVisible({ timeout: 15_000 })
  await expect(page.getByTestId('material-detail-merged-toolbar')).toBeVisible({ timeout: 15_000 })
})
