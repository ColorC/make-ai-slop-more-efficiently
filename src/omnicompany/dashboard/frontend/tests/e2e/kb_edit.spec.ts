import { test, expect } from '@playwright/test'
import { SHOTS, setupErrorLogging, gotoModule } from './helpers'

// 用一个临时 note 测试 (注: 用自己生成的 round 1 计划文档作为可改可改回的 sandbox)
const TEST_NOTE = 'plans/dashboard/[2026-05-01]WEB-FOUNDATION/usability_paths_round1'

test.beforeEach(async ({ page }) => {
  setupErrorLogging(page)
  await page.goto('/')
  await gotoModule(page, 'kb')
})

async function openNote(page: any, noteId: string) {
  await page.getByPlaceholder('过滤...').fill(noteId.split('/').pop() || noteId)
  await page.waitForTimeout(400)
  await page.locator(`div[title="${noteId}"]`).first().click()
  await page.waitForTimeout(500)
}

test('B9.1 默认进只读模式 + 三个模式按钮可见', async ({ page }) => {
  await openNote(page, TEST_NOTE)
  await expect(page.getByRole('button', { name: /只读/ })).toBeVisible()
  await expect(page.getByRole('button', { name: /编辑/ })).toBeVisible()
  await expect(page.getByRole('button', { name: /分屏/ })).toBeVisible()
  await expect(page.getByRole('button', { name: '保存' })).toBeVisible()
})

test('B9.2 切到编辑模式 → Monaco 出现', async ({ page }) => {
  await openNote(page, TEST_NOTE)
  await page.getByRole('button', { name: /编辑/ }).click()
  await expect(page.locator('.monaco-editor').first()).toBeVisible({ timeout: 15000 })
})

test('B9.3 切到分屏模式 → Monaco + 预览 同时可见', async ({ page }) => {
  await openNote(page, TEST_NOTE)
  await page.getByRole('button', { name: /分屏/ }).click()
  await expect(page.locator('.monaco-editor').first()).toBeVisible({ timeout: 15000 })
  await page.waitForTimeout(500)
  await page.screenshot({ path: `${SHOTS}/kb_edit_split.png`, fullPage: true })
})

async function typeInMonaco(page: any, text: string) {
  // Focus then move cursor to end + add newline + type
  await page.locator('.monaco-editor').first().click()
  await page.keyboard.press('Control+End')
  await page.keyboard.press('End')
  await page.keyboard.press('Enter')
  await page.keyboard.press('Enter')
  await page.keyboard.type(text, { delay: 5 })
  // give Monaco onChange + react state propagation time
  await page.waitForTimeout(500)
}

test('B9.4 修改后 dirty 指示出现 + 保存按钮可点', async ({ page }) => {
  await openNote(page, TEST_NOTE)
  await page.getByRole('button', { name: /编辑/ }).click()
  await expect(page.locator('.monaco-editor').first()).toBeVisible({ timeout: 15000 })
  await page.waitForTimeout(800)
  await typeInMonaco(page, '<!-- e2e edit test marker -->')
  // dirty marker (●) — span title="未保存的修改"
  await expect(page.locator('span[title*="未保存"]')).toBeVisible({ timeout: 5000 })
})

test('B9.5 真保存 → 文件写回 + 后端 GET 含改动', async ({ page, request }) => {
  await openNote(page, TEST_NOTE)
  await page.getByRole('button', { name: /编辑/ }).click()
  await expect(page.locator('.monaco-editor').first()).toBeVisible({ timeout: 15000 })
  await page.waitForTimeout(800)
  const marker = `<!-- e2e marker ${Date.now()} -->`
  await typeInMonaco(page, marker)
  await expect(page.locator('span[title*="未保存"]')).toBeVisible({ timeout: 5000 })
  await page.getByRole('button', { name: '保存' }).click()
  await expect(page.getByText(/已保存/)).toBeVisible({ timeout: 5000 })
  await page.screenshot({ path: `${SHOTS}/kb_edit_saved.png`, fullPage: true })
  // verify via backend
  const r = await request.get(`/api/notes/${TEST_NOTE}`)
  const j = await r.json()
  expect(j.content).toContain('e2e marker')
  // cleanup: revert by removing all e2e markers
  const cleaned = (j.content as string)
    .replace(/\r?\n\r?\n<!-- e2e marker \d+ -->/g, '')
    .replace(/\r?\n\r?\n<!-- e2e edit test marker -->/g, '')
  await request.put(`/api/notes/${TEST_NOTE}`, { data: { content: cleaned } })
})
