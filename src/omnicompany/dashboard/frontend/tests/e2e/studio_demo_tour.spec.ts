// 统一设计工作室引导演示 e2e(计划 §8.3 第 1 条 / 引导演示材料规范 § 中文-only gate)。
// 覆盖:URL 带 ?demo=studio-walker → 覆盖层挂载 → 演示卡出现 → 第一步 narration 可见 →
// 高亮命中真实 project-detail → 上/下步真实驱动 dashboard(点到材料轨迹画布)。
// 另验:不带 ?demo= 时零挂载(玩家/日常面零泄漏)。
//
// 数据前提:注册表里有 walker 项目(canvas 三轨/版本链已是真实验收数据)。
// 后端服起停由 global-setup 管;起不来如实报,不要求本次一定能跑通(照既有 spec 惯例)。

import { test, expect, type Page } from '@playwright/test'
import { setupErrorLogging } from './helpers'

test.beforeEach(async ({ page }) => {
  setupErrorLogging(page)
})

async function waitShell(page: Page): Promise<void> {
  await expect(page.getByTestId('cockpit-shell')).toBeVisible({ timeout: 20_000 })
}

test('?demo=studio-walker: 覆盖层挂载, 第一步 narration 可见, 真实驱动画布', async ({ page }) => {
  await page.goto('/?demo=studio-walker')
  await waitShell(page)

  // 覆盖层演示卡出现(wiki-core mountDemoTour 渲染 demo-card)
  const card = page.getByTestId('demo-card')
  await expect(card).toBeVisible({ timeout: 15_000 })

  // 第一步 narration 可见, 且是中文人话(不含比喻框架/英文术语)
  const narration = page.getByTestId('demo-narration')
  await expect(narration).toBeVisible()
  await expect(narration).toContainText('行者项目页')

  // 步骤计数器 = 1/N(第一步)
  await expect(page.getByTestId('demo-step-counter')).toContainText('1/')

  // 第一步的 eval:openProject 应打开真实 walker 项目页(高亮目标 project-detail)
  await expect(page.getByTestId('project-detail')).toBeVisible({ timeout: 20_000 })

  // 下一步 → 到"场景一 · 只看本项目", 高亮真实材料轨迹画布
  await page.getByTestId('demo-next').click()
  await expect(page.getByTestId('review-canvas')).toBeVisible({ timeout: 15_000 })
  await expect(page.getByTestId('demo-step-counter')).toContainText('2/')

  // 每步可评论:评论按钮存在(comments store 已挂), 点开评论面板出现
  await page.getByTestId('demo-comment').click()
  await expect(page.getByTestId('demo-comment-panel')).toBeVisible()
  await expect(page.getByTestId('demo-comment-input')).toBeVisible()
})

test('不带 ?demo= 时覆盖层零挂载(玩家/日常面零泄漏)', async ({ page }) => {
  await page.goto('/')
  await waitShell(page)
  // 挂载钩容器在, 但里面没有演示卡(未激活)
  await expect(page.getByTestId('studio-demo-mount')).toBeAttached()
  await expect(page.getByTestId('demo-card')).toHaveCount(0)
})

test('iframe 里打开演示地址 → 套娃守卫接管, 不播嵌套演示(2026-07-04 用户反馈回归)', async ({ page }) => {
  // 复现路径:审阅台把演示材料的 live_url 嵌 iframe → 驾驶舱嵌进驾驶舱。
  await page.goto('/')
  await waitShell(page)
  await page.evaluate(() => {
    const f = document.createElement('iframe')
    f.src = '/?demo=studio-walker'
    f.setAttribute('data-testid', 'nesting-probe')
    f.style.cssText = 'position:fixed;inset:10%;width:80%;height:80%;z-index:99999'
    document.body.appendChild(f)
  })
  const inner = page.frameLocator('[data-testid="nesting-probe"]')
  // 守卫面板出现, 带"在新标签页打开"出口;嵌套上下文里绝不渲染演示卡
  await expect(inner.getByTestId('studio-demo-nested-guard')).toBeVisible({ timeout: 20_000 })
  await expect(inner.getByTestId('studio-demo-open-top')).toBeVisible()
  await expect(inner.getByTestId('demo-card')).toHaveCount(0)
})
