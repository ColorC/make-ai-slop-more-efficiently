/**
 * review_copy_path.spec.ts — 审阅台「复制材料路径」真 UI e2e (2026-07-05 用户: 看到材料
 * 却无法一键复制路径递给其他 agent)。
 *
 * 链路: 注入材料 → 驾驶舱审阅队列选中 → MaterialDetail「更多」菜单 → 复制材料路径 →
 *   剪贴板 = GET /{id}/path 的 file_abs_path + surface-notice 可见反馈。
 * 另验证 inline 材料(无落盘文件)退化: 剪贴板 = 内容 API 地址。
 *
 * 跑前提: daemon (8201) + dashboard (8200) 都活 (global-setup 自启); 不调 LLM。
 */

import { test, expect, type APIRequestContext, type Page } from '@playwright/test'
import { setupErrorLogging } from './helpers'

const DASHBOARD = `http://127.0.0.1:${process.env.OMNI_E2E_DASHBOARD_PORT || '8200'}`
const PLAN_TAG = 'e2e/review_copy_path'

async function createMaterial(request: APIRequestContext, body: Record<string, unknown>): Promise<any> {
  const r = await request.post(`${DASHBOARD}/api/boss-sight/reviewstage`, { data: body })
  expect(r.ok(), `create material: ${r.status()} ${await r.text()}`).toBeTruthy()
  return await r.json()
}

async function cleanup(request: APIRequestContext) {
  const r = await request.get(`${DASHBOARD}/api/boss-sight/reviewstage`)
  const { items } = await r.json()
  for (const m of items) {
    if ((m.source_plan_id || '') === PLAN_TAG) {
      try { await request.delete(`${DASHBOARD}/api/boss-sight/reviewstage/${m.id}`) } catch { /* */ }
    }
  }
}

async function openAndSelect(page: Page, materialId: string) {
  await page.goto(`${DASHBOARD}/?open_type=review_queue&open_id=main`)
  await page.locator('[data-testid="review-queue-panel"]').waitFor({ timeout: 15_000 })
  await page.locator(`[data-testid="material-card-${materialId}"]`).click()
  await page.locator('[data-testid="review-queue-detail"] [data-testid="material-detail"]').waitFor({ timeout: 5_000 })
}

test.describe('审阅台「更多」→ 复制材料路径', () => {
  test.use({ permissions: ['clipboard-read', 'clipboard-write'] })

  test.beforeEach(async ({ page, request }) => {
    setupErrorLogging(page)
    await cleanup(request)
  })

  test.afterEach(async ({ request }) => {
    await cleanup(request)
  })

  test('文件材料: 剪贴板 = 落盘绝对路径 + 可见反馈', async ({ page, request }) => {
    const m = await createMaterial(request, {
      kind: 'markdown', tier: 'important',
      title: '复制路径e2e·文件材料',
      source_plan_id: PLAN_TAG,
      project: 'unfiled', track: '工作报告', version: 1,
      file_relpath: 'files/e2e_review_copy_path.md',
      inline_content: '# 复制路径 e2e\n\n这条材料有落盘文件, 复制的应是它的绝对路径。',
    })
    // 期望值从同一后端契约取(不硬编码机器路径)
    const pr = await request.get(`${DASHBOARD}/api/boss-sight/reviewstage/${m.id}/path`)
    expect(pr.ok(), `get path: ${pr.status()}`).toBeTruthy()
    const { file_abs_path } = await pr.json()
    expect(file_abs_path, 'file 材料应有绝对路径').toBeTruthy()

    await openAndSelect(page, m.id)
    await page.getByTestId('material-detail-more').click()
    await expect(page.getByTestId('material-detail-more-menu')).toBeVisible()
    await page.getByTestId('review-copy-path').click()

    await expect(page.getByTestId('surface-notice')).toContainText('已复制文件路径')
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(file_abs_path)
  })

  test('inline 材料(无落盘文件): 退化复制内容 API 地址', async ({ page, request }) => {
    const m = await createMaterial(request, {
      kind: 'markdown', tier: 'important',
      title: '复制路径e2e·内联材料',
      source_plan_id: PLAN_TAG,
      project: 'unfiled', track: '工作报告', version: 1,
      inline_content: '# inline\n\n这条材料没有落盘文件。',
    })

    await openAndSelect(page, m.id)
    await page.getByTestId('material-detail-more').click()
    await page.getByTestId('review-copy-path').click()

    await expect(page.getByTestId('surface-notice')).toContainText('内容 API 地址')
    const copied = await page.evaluate(() => navigator.clipboard.readText())
    expect(copied).toContain(`/api/boss-sight/reviewstage/${m.id}/file`)
  })
})
