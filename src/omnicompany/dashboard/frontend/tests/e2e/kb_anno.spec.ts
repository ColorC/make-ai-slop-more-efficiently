import { test, expect } from '@playwright/test'
import { SHOTS, setupErrorLogging, gotoModule } from './helpers'

const TEST_NOTE = 'plans/dashboard/[2026-05-01]WEB-FOUNDATION/usability_paths_round1'

// FNV-1a 32-bit hash matching frontend `paragraphHash` for test fixtures.
function paragraphHash(text: string): string {
  const norm = (text || '').toLowerCase().replace(/\s+/g, ' ').trim().slice(0, 200)
  let h = 0x811c9dc5
  for (let i = 0; i < norm.length; i++) {
    h ^= norm.charCodeAt(i)
    h = (h * 0x01000193) >>> 0
  }
  return h.toString(16)
}

test.beforeEach(async ({ page, request }) => {
  setupErrorLogging(page)
  // Ensure clean state on the test note
  const r = await request.get(`/api/notes/${TEST_NOTE}/annotations`)
  if (r.ok()) {
    const j = await r.json()
    for (const a of j.items || []) {
      await request.delete(`/api/notes/${TEST_NOTE}/annotations/${a.id}`)
    }
  }
  await page.goto('/')
  await gotoModule(page, 'kb')
})

async function openTestNote(page: any) {
  await page.getByPlaceholder('过滤...').fill('usability_paths_round1')
  await page.waitForTimeout(400)
  await page.locator(`div[title="${TEST_NOTE}"]`).first().click()
  await page.waitForTimeout(800)
}

test('B7.1 批注 sidebar section 默认显示 0 + 提示', async ({ page }) => {
  await openTestNote(page)
  await expect(page.getByText(/批注 · 0/)).toBeVisible({ timeout: 10000 })
  await expect(page.getByText(/无批注/)).toBeVisible()
})

test('B7.2 hover 段落右侧出 + 入口', async ({ page }) => {
  await openTestNote(page)
  // first paragraph (".markdown-body p") in view mode
  const para = page.locator('.markdown-body p').first()
  await para.waitFor({ timeout: 10000 })
  await para.hover()
  await page.waitForTimeout(200)
  await expect(page.locator('span[title="添加批注"]').first()).toBeVisible()
})

test('B7.3 通过 API 造批注 → sidebar 立显', async ({ page, request }) => {
  // POST anno via API for known paragraph snippet "时间: 2026-05-01"
  // The actual paragraph the editor sees: rendered text only. Using snippet match.
  const text = '时间: 2026-05-01'
  const hash = paragraphHash(text)
  await request.post(`/api/notes/${TEST_NOTE}/annotations`, {
    data: { anchor: { hash, snippet: text }, comment: 'API 创建的测试批注' },
  })
  await openTestNote(page)
  await expect(page.getByText(/批注 · 1/)).toBeVisible({ timeout: 10000 })
  await expect(page.getByText('API 创建的测试批注')).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/kb_anno_sidebar.png`, fullPage: true })
})

test('B7.4 点 sidebar 批注项 → modal 出 + 含已有评论 + 删', async ({ page, request }) => {
  const text = '时间: 2026-05-01'
  const hash = paragraphHash(text)
  await request.post(`/api/notes/${TEST_NOTE}/annotations`, {
    data: { anchor: { hash, snippet: text }, comment: '可删除的批注' },
  })
  await openTestNote(page)
  await page.locator('text=可删除的批注').first().click()
  await expect(page.getByText(/批注 · \d+ 条|添加批注/).first()).toBeVisible({ timeout: 5000 })
  // 删除
  page.on('dialog', (d) => d.accept())
  await page.getByRole('button', { name: '删除' }).first().click()
  await page.waitForTimeout(800)
  await expect(page.getByText(/批注 · 0/)).toBeVisible({ timeout: 5000 })
})

test('B7.5 失锚批注 (hash 不存在) 用 ⚠ 标 + 算入失锚计数', async ({ page, request }) => {
  await request.post(`/api/notes/${TEST_NOTE}/annotations`, {
    data: { anchor: { hash: 'deadbeef', snippet: '某个已经不在的段落...' }, comment: '失锚测试' },
  })
  await openTestNote(page)
  // Header includes "1 失锚" pattern
  await expect(page.getByText(/\d+ 失锚/)).toBeVisible({ timeout: 10000 })
  await expect(page.locator('text=失锚测试').first()).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/kb_anno_orphan.png`, fullPage: true })
})

test.afterAll(async ({ request }) => {
  // final cleanup
  const r = await request.get(`/api/notes/${TEST_NOTE}/annotations`)
  if (r.ok()) {
    const j = await r.json()
    for (const a of j.items || []) {
      await request.delete(`/api/notes/${TEST_NOTE}/annotations/${a.id}`)
    }
  }
})
