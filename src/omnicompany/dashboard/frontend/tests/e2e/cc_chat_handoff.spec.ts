/**
 * cc_chat_handoff.spec.ts — 人用聊天迁到收编 chatui 后的驾驶舱"落点卡"真 UI e2e。
 *
 * 背景: 手搓聊天 UI(CcChatPanel/ChatInterface/ChatStandalone)已整体删除, 驾驶舱内不再嵌
 * 聊天面板。所有 chat 入口(总控对话 + 普通 cc_session chat 页签)改渲染 ChatuiHandoff 落点卡,
 * 点按钮 window.open 到收编 chatui(:7348); 总控带 ?provider=controller 预选。
 *
 * 两个场景(从页面真实点击驱动, 非 API 探针):
 *   A 总控对话 tab → 落点卡可见 + 按钮 window.open 带 provider=controller
 *   B 普通 cc_session chat 页签 → 落点卡可见(无 provider=controller)
 *
 * 跑: OMNI_E2E_DASHBOARD_PORT=8210 npx playwright test cc_chat_handoff.spec.ts
 *   (端口占用时 global-setup 复用已起的控制面 + ccdaemon)
 *
 * 替代已删的 boss_sight_controller / cc_chat_resilience / cc_progressive_context 三个测手搓聊天的 spec。
 */

import { test, expect, type APIRequestContext } from '@playwright/test'
import { setupErrorLogging } from './helpers'

// window.open 桩: 记录最后一次 open 的 URL, 不真开 chatui 页(避免重型加载 + 弹窗), 断言可控。
async function stubWindowOpen(page: import('@playwright/test').Page): Promise<void> {
  await page.addInitScript(() => {
    ;(window as unknown as { __lastOpen: string | null }).__lastOpen = null
    window.open = ((url?: string | URL) => {
      ;(window as unknown as { __lastOpen: string | null }).__lastOpen = String(url ?? '')
      return null
    }) as typeof window.open
  })
}

const lastOpen = (page: import('@playwright/test').Page) =>
  page.evaluate(() => (window as unknown as { __lastOpen: string | null }).__lastOpen)

test.beforeEach(async ({ page }) => {
  setupErrorLogging(page)
  await stubWindowOpen(page)
})

test('A 总控对话 tab → chatui 落点卡 + window.open 带 provider=controller', async ({ page }) => {
  // 深链开总控编辑页签(open_type=controller, 同 Briefing「subagents」入口)。
  await page.goto('/?open_type=controller&open_id=main&open_title=%E6%80%BB%E6%8E%A7')

  // 切到"总控对话"视图
  const chatTab = page.locator('[data-testid="controller-view-chat"]')
  await chatTab.waitFor({ timeout: 15_000 })
  await chatTab.click()

  // 落点卡 + 按钮可见
  await expect(page.locator('[data-testid="cc-chat-chatui-handoff"]')).toBeVisible({ timeout: 15_000 })
  const btn = page.locator('[data-testid="cc-chat-open-chatui"]')
  await expect(btn).toBeVisible()
  await expect(btn).toContainText('总控')

  // 点按钮 → window.open 到 chatui(:7348) 且预选 controller
  await btn.click()
  const opened = await lastOpen(page)
  expect(opened, `window.open url = ${opened}`).toContain('7348')
  expect(opened).toContain('provider=controller')
})

test('B 普通 cc_session chat 页签 → chatui 落点卡(无 provider=controller)', async ({ page, request }) => {
  const sess = await createChatSession(request)
  try {
    await page.goto(`/?open_type=cc_session&open_id=${encodeURIComponent(sess.id)}&open_title=chat`)

    await expect(page.locator('[data-testid="cc-chat-chatui-handoff"]')).toBeVisible({ timeout: 15_000 })
    const btn = page.locator('[data-testid="cc-chat-open-chatui"]')
    await expect(btn).toBeVisible()

    await btn.click()
    const opened = await lastOpen(page)
    expect(opened, `window.open url = ${opened}`).toContain('7348')
    expect(opened).not.toContain('provider=controller')
  } finally {
    try { await request.delete(`/api/cc/chat/sessions/${sess.id}`) } catch { /* 清理失败不阻断 */ }
  }
})

async function createChatSession(request: APIRequestContext): Promise<{ id: string }> {
  const r = await request.post('/api/cc/chat/sessions', {
    data: { provider: 'claude_code', cwd: 'C:/workspace/omnicompany' },
  })
  expect(r.ok(), `create chat session: ${r.status()} ${await r.text()}`).toBeTruthy()
  return await r.json()
}
