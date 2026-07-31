/**
 * boss_sight_reviewstage_visual.spec.ts — BOSS SIGHT 审阅链路视觉 e2e (驾驶舱版).
 *
 * R4 起 standalone (/review-stage) 已退役, 本 spec 改走驾驶舱真 UI 路径:
 *   deeplink 开 review_queue 页签 → 共享 MaterialSidebar 选材料 →
 *   右列共享 MaterialDetail (verdict/评论/批注) → "Detail" 开 review_material 页签。
 * 另验证老链接 /review-stage?material=X 经 entryRoute 重定向落进驾驶舱 (老链接不死)。
 *
 * 跑前提: daemon (8201) + dashboard (8200) 都活 (global-setup 自启); 不调 LLM。
 * 截图存档: tests/screenshots/block5/<step>.png
 */

import { test, expect, type APIRequestContext, type Page } from '@playwright/test'
import { mkdirSync } from 'fs'
import { setupErrorLogging } from './helpers'

const SHOTS_DIR = 'tests/screenshots/block5'
const DASHBOARD = `http://127.0.0.1:${process.env.OMNI_E2E_DASHBOARD_PORT || '8200'}`

mkdirSync(SHOTS_DIR, { recursive: true })


async function createMaterial(request: APIRequestContext, body: Record<string, unknown>): Promise<any> {
  const r = await request.post(`${DASHBOARD}/api/boss-sight/reviewstage`, { data: body })
  expect(r.ok(), `create material: ${r.status()} ${await r.text()}`).toBeTruthy()
  return await r.json()
}


async function deleteMaterial(request: APIRequestContext, id: string) {
  try { await request.delete(`${DASHBOARD}/api/boss-sight/reviewstage/${id}`) } catch { /* */ }
}


async function listAll(request: APIRequestContext): Promise<{ items: any[] }> {
  const r = await request.get(`${DASHBOARD}/api/boss-sight/reviewstage`)
  return await r.json()
}


/** 驾驶舱 deeplink 打开审阅队列单例页签, 等共享 MaterialSidebar 挂出来。 */
async function openReviewQueue(page: Page) {
  await page.goto(`${DASHBOARD}/?open_type=review_queue&open_id=main`)
  await page.locator('[data-testid="review-queue-panel"]').waitFor({ timeout: 15_000 })
  await page.locator('[data-testid="material-sidebar"]').waitFor({ timeout: 10_000 })
}


test.describe('BOSS-SIGHT · 驾驶舱审阅链路视觉', () => {

  test.beforeEach(async ({ page, request }) => {
    setupErrorLogging(page)
    // 清空 stale materials (允许重跑)
    const { items } = await listAll(request)
    for (const m of items) {
      if ((m.source_plan_id || '').startsWith('e2e/block5_visual')) {
        await deleteMaterial(request, m.id)
      }
    }
  })

  test('B5-V01 · deeplink 开审阅队列页签看空状态', async ({ page }) => {
    await openReviewQueue(page)
    // 驾驶舱壳 + 队列面板要素可见
    await expect(page.locator('[data-testid="cockpit-shell"]')).toBeVisible()
    await expect(page.locator('[data-testid="review-queue-refresh"]')).toBeVisible()
    await page.screenshot({ path: `${SHOTS_DIR}/01_empty_state.png`, fullPage: true })
  })

  test('B5-V02 · 注入 markdown material → 共享 sidebar 显示', async ({ page, request }) => {
    const m = await createMaterial(request, {
      kind: 'markdown', tier: 'important',
      title: 'V02 测试 markdown',
      source_plan_id: 'e2e/block5_visual',
      inline_content: '# Hello from V02\n\nThis is a **markdown** material with `code`.\n\n- item 1\n- item 2',
    })

    await openReviewQueue(page)
    await page.locator(`[data-testid="material-card-${m.id}"]`).waitFor({ timeout: 10_000 })
    await expect(page.locator(`[data-testid="material-card-${m.id}"]`)).toContainText('V02 测试 markdown')

    await page.screenshot({ path: `${SHOTS_DIR}/02_with_material.png`, fullPage: true })
  })

  test('B5-V03 · 选材料 → 共享明细渲染 + verdict/评论/批注 → Detail 开 review_material 页签', async ({ page, request }) => {
    const m = await createMaterial(request, {
      kind: 'markdown', tier: 'important',
      title: 'V03 详情视图',
      source_plan_id: 'e2e/block5_visual',
      inline_content: '# V03 详情\n\n这条 material 用于验证: 主区渲染 + verdict 按钮 + 右侧评论 panel.',
    })
    // 加一条 AI 批注让批注 panel 有内容
    await request.post(`${DASHBOARD}/api/boss-sight/reviewstage/${m.id}/annotation`, {
      data: { content: 'AI: 段落 1 可以更精炼', kind: 'ai', author: 'controller' },
    })

    await openReviewQueue(page)
    await page.locator(`[data-testid="material-card-${m.id}"]`).click()
    // 右列 = 共享 MaterialDetail (与 review_material 面板同一份)
    await page.locator('[data-testid="review-queue-detail"] [data-testid="material-detail"]').waitFor({ timeout: 5_000 })
    await expect(page.locator('[data-testid="material-markdown"]')).toBeVisible()
    await expect(page.locator('[data-testid="verdict-accept"]')).toBeVisible()
    await expect(page.locator('[data-testid="verdict-reject"]')).toBeVisible()
    // R5: 评论改为"每材料一个 markdown 文件"的宽视图, 在"评论"页签下(默认显示内容)。
    await page.locator('[data-testid="material-mode-comments"]').click()
    await expect(page.locator('[data-testid="comment-input"]')).toBeVisible()
    // AI 批注在评论视图顶部只读显示
    await expect(page.locator('[data-testid="annotation-item"]').first()).toContainText('段落 1 可以更精炼')
    await page.screenshot({ path: `${SHOTS_DIR}/03_material_detail.png`, fullPage: true })

    // "Detail" 按钮 → 单条材料独立页签 (review_material)
    await page.locator('[data-testid="review-queue-open-detail-tab"]').click()
    await page.locator('[data-testid="review-material-panel"]').waitFor({ timeout: 10_000 })
    await expect(page.locator('[data-testid="review-material-panel"] [data-testid="material-detail"]')).toBeVisible()
    await expect(page.locator('[data-testid="review-material-panel"]')).toContainText('V03 详情视图')
    await page.screenshot({ path: `${SHOTS_DIR}/04_material_tab.png`, fullPage: true })
  })

  test('B5-V04 · 老链接 /review-stage?material=X 重定向进驾驶舱材料页签', async ({ page, request }) => {
    const m = await createMaterial(request, {
      kind: 'markdown', tier: 'important',
      title: 'V04 老链接兼容',
      source_plan_id: 'e2e/block5_visual',
      inline_content: '# V04\n\n老 /review-stage 链接应映射成驾驶舱 deeplink.',
    })

    await page.goto(`${DASHBOARD}/review-stage?material=${m.id}`)
    await page.locator('[data-testid="review-material-panel"]').waitFor({ timeout: 15_000 })
    expect(page.url()).not.toContain('/review-stage')
    expect(page.url()).toContain(`open_id=${m.id}`)
    await expect(page.locator('[data-testid="review-material-panel"]')).toContainText('V04 老链接兼容')
    await page.screenshot({ path: `${SHOTS_DIR}/05_legacy_link_material.png`, fullPage: true })
  })

  test('B5-V05 · 老链接 /review-stage (无参) 重定向进审阅队列', async ({ page }) => {
    await page.goto(`${DASHBOARD}/review-stage`)
    await page.locator('[data-testid="review-queue-panel"]').waitFor({ timeout: 15_000 })
    expect(page.url()).not.toContain('/review-stage')
    await expect(page.locator('[data-testid="material-sidebar"]')).toBeVisible()
    await page.screenshot({ path: `${SHOTS_DIR}/06_legacy_link_queue.png`, fullPage: true })
  })

  test('B5-V06 · 必验收级 mandatory + html 渲染 + stats 警告', async ({ page, request }) => {
    const m = await createMaterial(request, {
      kind: 'html', tier: 'mandatory',
      title: 'V06 必验收级网页',
      source_plan_id: 'e2e/block5_visual',
      inline_content: '<!doctype html><html><body><h1>必验收</h1><p>这是要拦住后续 spawn 的关键产物</p></body></html>',
    })

    await openReviewQueue(page)
    // 共享 sidebar 顶部 stats 应显未审 mandatory 数
    await expect(page.locator('[data-testid="stats-mandatory-unaccepted"]')).toBeVisible({ timeout: 10_000 })
    await page.locator(`[data-testid="material-card-${m.id}"]`).click()
    // html iframe 真渲染
    await expect(page.locator('[data-testid="material-html"]')).toBeVisible({ timeout: 5_000 })
    await page.screenshot({ path: `${SHOTS_DIR}/07_mandatory_html.png`, fullPage: true })
  })
})
