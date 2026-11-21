import { test, expect } from '@playwright/test'
import { SHOTS, setupErrorLogging, openCmdK } from './helpers'

const DEMO = '_sandbox/kb_markdown_demo'

test.beforeEach(async ({ page }) => {
  setupErrorLogging(page)
  await page.goto('/')
  await openCmdK(page, 'kb_markdown_demo')
  await expect(page.locator('.markdown-body h1').first()).toBeVisible({ timeout: 15000 })
  // scroll to section 12 (last section)
  await page.locator('.markdown-body').first().evaluate((el) => el.scrollTo(0, el.scrollHeight))
  await page.waitForTimeout(400)
})

// ─── Standard markdown link routing ───────────────────────────────────────────

test('ML.1 外部 URL 链接 → target=_blank 在新 tab 打开', async ({ page }) => {
  const a = page.locator('.markdown-body a', { hasText: 'example.com' }).first()
  await expect(a).toBeVisible()
  expect(await a.getAttribute('target')).toBe('_blank')
  expect(await a.getAttribute('href')).toContain('https://example.com')
})

test('ML.2 docs/X.md 链接 → 路由到 note tab (不是 404)', async ({ page }) => {
  const a = page.locator('.markdown-body a[data-md-link]', { hasText: 'terminology' }).first()
  await expect(a).toBeVisible({ timeout: 5000 })
  // critical: must be intercepted (not raw href to docs/.../foo.md)
  expect(await a.getAttribute('data-md-link')).toBe('standards/terminology')
  await a.click()
  await page.waitForTimeout(800)
  // a dockview tab labelled 'terminology' should appear in central area
  await expect(page.locator('.dv-tab', { hasText: /terminology/i }).first()).toBeVisible({ timeout: 5000 })
})

test('ML.3 docs 前缀省略的 .md 链接也路由', async ({ page }) => {
  const a = page.locator('.markdown-body a[data-md-link]', { hasText: 'llm_first' }).first()
  await expect(a).toBeVisible()
  expect(await a.getAttribute('data-md-link')).toBe('standards/llm_first')
})

test('ML.4 ./ 相对链接基于当前 note 路径解析', async ({ page }) => {
  // demo at _sandbox/kb_markdown_demo, ./kb_markdown_demo.md → _sandbox/kb_markdown_demo
  const a = page.locator('.markdown-body a[data-md-link]', { hasText: 'sibling demo' }).first()
  await expect(a).toBeVisible()
  expect(await a.getAttribute('data-md-link')).toBe('_sandbox/kb_markdown_demo')
})

test('ML.5 ../ 相对链接走父 dir', async ({ page }) => {
  // _sandbox/kb_markdown_demo + ../PROGRESS.md → PROGRESS
  const a = page.locator('.markdown-body a[data-md-link]', { hasText: 'PROGRESS' }).first()
  await expect(a).toBeVisible()
  expect(await a.getAttribute('data-md-link')).toBe('PROGRESS')
})

test('ML.6 mailto 走外部新 tab', async ({ page }) => {
  const a = page.locator('.markdown-body a', { hasText: 'mailto' }).first()
  await expect(a).toBeVisible()
  expect(await a.getAttribute('target')).toBe('_blank')
  expect(await a.getAttribute('href')).toContain('mailto:')
})

test('ML.7 #anchor 不走外部 tab (浏览器默认锚跳转)', async ({ page }) => {
  const a = page.locator('.markdown-body a', { hasText: '跳到 4 节表格' }).first()
  await expect(a).toBeVisible()
  // not _blank
  const target = await a.getAttribute('target')
  expect(target === null || target === '').toBeTruthy()
  expect(await a.getAttribute('href')).toMatch(/^#/)
})

// ─── Preview font size control ────────────────────────────────────────────────

test('ML.8 字号工具栏可见 + 显当前字号', async ({ page }) => {
  const ctl = page.locator('[data-md-fontctl]').first()
  await expect(ctl).toBeVisible()
  await expect(ctl.locator('[data-md-fontctl-minus]')).toBeVisible()
  await expect(ctl.locator('[data-md-fontctl-plus]')).toBeVisible()
})

test('ML.9 点 A+ 字号变大 + .markdown-body inline fontSize 同步', async ({ page }) => {
  const body = page.locator('.markdown-body').first()
  const before = Number(await body.getAttribute('data-preview-fontsize'))
  await page.locator('[data-md-fontctl-plus]').first().click()
  await page.waitForTimeout(100)
  const after = Number(await body.getAttribute('data-preview-fontsize'))
  expect(after).toBe(before + 1)
  // also reflected in inline style
  const inlineFs = await body.evaluate((el) => (el as HTMLElement).style.fontSize)
  expect(inlineFs).toBe(`${after}px`)
})

test('ML.10 点 A− 字号变小', async ({ page }) => {
  const body = page.locator('.markdown-body').first()
  // bump up first to give room (in case at min)
  await page.locator('[data-md-fontctl-plus]').first().click()
  await page.locator('[data-md-fontctl-plus]').first().click()
  await page.waitForTimeout(100)
  const before = Number(await body.getAttribute('data-preview-fontsize'))
  await page.locator('[data-md-fontctl-minus]').first().click()
  await page.waitForTimeout(100)
  const after = Number(await body.getAttribute('data-preview-fontsize'))
  expect(after).toBe(before - 1)
})

test('ML.11 字号持久 (跨 tab 同 store + reload 保持)', async ({ page }) => {
  // bump to a distinctive size
  for (let i = 0; i < 3; i++) await page.locator('[data-md-fontctl-plus]').first().click()
  await page.waitForTimeout(100)
  const body = page.locator('.markdown-body').first()
  const fs = Number(await body.getAttribute('data-preview-fontsize'))
  // reload preserves via localStorage
  await page.reload()
  await page.waitForTimeout(800)
  await expect(page.locator('.markdown-body').first()).toBeVisible({ timeout: 8000 })
  const after = Number(await page.locator('.markdown-body').first().getAttribute('data-preview-fontsize'))
  expect(after).toBe(fs)
  // restore default for next test
  await page.locator('[data-md-fontctl-reset]').first().click()
  await page.screenshot({ path: `${SHOTS}/kb_md_fontctl.png`, fullPage: false })
})

test('ML.12 点 reset 按钮回默认', async ({ page }) => {
  // bump up some
  for (let i = 0; i < 5; i++) await page.locator('[data-md-fontctl-plus]').first().click()
  await page.waitForTimeout(100)
  await page.locator('[data-md-fontctl-reset]').first().click()
  await page.waitForTimeout(100)
  const body = page.locator('.markdown-body').first()
  const fs = Number(await body.getAttribute('data-preview-fontsize'))
  expect(fs).toBe(13) // default
})
