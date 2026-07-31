import { test, expect } from '@playwright/test'
import { setupErrorLogging, gotoModule } from './helpers'

/**
 * S16: cc_session right panel — 3 structural sections
 *   上下文 / 修改记录 / 新增产出
 * Plus polled refresh + jump-to-plan / jump-to-note clickability.
 */

test.beforeEach(async ({ page }) => {
  setupErrorLogging(page)
  await page.goto('/')
  await gotoModule(page, 'agent')
  await page.waitForTimeout(400)
})

async function spawnCmdSession(request: any): Promise<string> {
  // Retry up to 3 times — winpty on Windows can spawn a session that dies within
  // a few ms (intermittent race during repeated spawn/kill cycles in tests).
  // We re-spawn until the session is still alive after a 1s settling period.
  for (let attempt = 0; attempt < 3; attempt++) {
    const r = await request.post('http://127.0.0.1:8200/api/cc/sessions', {
      data: { cmd: ['cmd.exe'], cwd: 'C:\\', cols: 100, rows: 30 },
    })
    if (!r.ok()) { await new Promise(r => setTimeout(r, 300)); continue }
    const sid = (await r.json()).id
    // Settle 1s, then verify still alive
    await new Promise(r => setTimeout(r, 1000))
    const list = await (await request.get('http://127.0.0.1:8200/api/cc/sessions')).json()
    if ((list.items || []).find((it: any) => it.id === sid && it.alive)) return sid
  }
  throw new Error('spawnCmdSession: session died within 1s on all 3 attempts (winpty race)')
}

test.afterEach(async ({ request }) => {
  // best-effort cleanup
  try {
    const r = await request.get('http://127.0.0.1:8200/api/cc/sessions')
    const items = (await r.json()).items as { id: string; cmd: string[] }[]
    for (const it of items) {
      if (it.cmd?.[0]?.toLowerCase()?.endsWith('cmd.exe')) {
        await request.delete(`http://127.0.0.1:8200/api/cc/sessions/${it.id}`)
      }
    }
  } catch { /* */ }
})

/** Open the cc_session tab via URL deep-link (bypasses sidebar's winpty-race-prone click flow). */
async function openCcTabViaDeepLink(page: any, sid: string): Promise<void> {
  const layout = Buffer.from(JSON.stringify({
    tabs: [{ type: 'cc_session', id: sid, title: `cmd · ${sid.slice(0, 8)}` }],
    active: `cc_session:${sid}`,
  }), 'utf-8').toString('base64')
  await page.goto(`/?layout=${layout}`)
  await page.waitForTimeout(800)
}

test('S16.1 cc_session 中央 tab 含 context panel + 三段', async ({ page, request }) => {
  const sid = await spawnCmdSession(request)
  await openCcTabViaDeepLink(page, sid)
  const panel = page.locator(`[data-session-context-panel][data-session-id="${sid}"]`)
  await expect(panel).toBeVisible({ timeout: 8000 })
  await expect(panel.locator('[data-ctx-section="context"]')).toBeVisible()
  await expect(panel.locator('[data-ctx-section="modified"]')).toBeVisible()
  await expect(panel.locator('[data-ctx-section="added"]')).toBeVisible()
})

test('S16.2 上下文段含 cwd 字段', async ({ page, request }) => {
  const sid = await spawnCmdSession(request)
  await openCcTabViaDeepLink(page, sid)
  const panel = page.locator(`[data-session-context-panel][data-session-id="${sid}"]`)
  await expect(panel).toBeVisible({ timeout: 8000 })
  await expect(panel.locator('[data-ctx-cwd]')).toContainText(/C:|c:|\\/i)
})

test('S16.3 GET /context 端点对未知 sid 回空但不 500', async ({ request }) => {
  const r = await request.get('http://127.0.0.1:8200/api/cc/sessions/zzz_nonexistent_xyz/context')
  expect(r.ok()).toBeTruthy()
  const j = await r.json()
  expect(j.session_id).toBe('zzz_nonexistent_xyz')
  expect(j.modified_files).toEqual([])
  expect(j.added_workers).toEqual([])
})

// S16.4 (REMOVED 2026-05-02 round 4): PATCH /context 端点已删
// work_type / standards 改走 plan.md frontmatter (跟 native session 统一)
// 验证 plan_meta 字段在 context endpoint 出现的测在 cc_context_panel.spec.ts S16.5 / S16.6 (待加)
// 跟 native_session_info.spec.ts 一起重写共享 SessionContextPanel kind=cc/native
