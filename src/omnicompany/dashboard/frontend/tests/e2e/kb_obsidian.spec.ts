import { test, expect } from '@playwright/test'
import { setupErrorLogging, openCmdK } from './helpers'

/**
 * S20 — Obsidian-flavored markdown coverage audit.
 * All features must render correctly in kb_markdown_demo §15.
 */

test.beforeEach(async ({ page }) => {
  setupErrorLogging(page)
  await page.goto('/')
  await openCmdK(page, 'kb_markdown_demo')
  await expect(page.locator('.markdown-body h1').first()).toBeVisible({ timeout: 15000 })
  // scroll to §15 (last section)
  await page.locator('.markdown-body').first().evaluate((el) => el.scrollTo(0, el.scrollHeight))
  await page.waitForTimeout(400)
})

test('OB.1 highlight ==text== → <mark> 渲染 + 自定义背景色', async ({ page }) => {
  const marks = page.locator('.markdown-body mark.obs-highlight')
  expect(await marks.count()).toBeGreaterThanOrEqual(2)
  const first = marks.first()
  await expect(first).toBeVisible()
  // background should be set (not transparent)
  const bg = await first.evaluate((el) => getComputedStyle(el).backgroundColor)
  // any non-zero rgba shows it's styled
  expect(bg).not.toBe('rgba(0, 0, 0, 0)')
  expect(bg).not.toBe('transparent')
})

test('OB.2 comment %% ... %% 完全消失', async ({ page }) => {
  const body = await page.locator('.markdown-body').first().innerText()
  // The probe string only appears inside the comment block in the source;
  // if it shows in rendered output, the comment stripper failed.
  expect(body).not.toContain('S20-PROBE-XYZQ')
})

test('OB.3 footnote [^demo-fn] 渲染 + 文末出脚注定义', async ({ page }) => {
  // remark-gfm renders footnote ref as <sup><a href="#user-content-fn-..."> + a list at the bottom
  const body = page.locator('.markdown-body')
  // footnote sup link
  const fnRef = body.locator('a[href^="#user-content-fn"]').first()
  await expect(fnRef).toBeVisible({ timeout: 5000 })
  // footnote section at bottom (class "footnotes" added by remark-gfm)
  const footnotes = body.locator('section.footnotes')
  await expect(footnotes).toBeVisible()
  await expect(footnotes).toContainText(/这是脚注内容/)
})

test('OB.4 HTML inline: <sup> <sub> <kbd> 都渲染', async ({ page }) => {
  const body = page.locator('.markdown-body')
  await expect(body.locator('sup').first()).toBeVisible()
  await expect(body.locator('sub').first()).toBeVisible()
  await expect(body.locator('kbd').first()).toBeVisible()
  await expect(body.locator('kbd').first()).toContainText(/Ctrl|Cmd/)
})

test('OB.5 <details> 折叠区可见, summary 默认折叠', async ({ page }) => {
  const det = page.locator('.markdown-body details').first()
  await expect(det).toBeVisible()
  // by default closed → open attr absent
  const isOpen = await det.evaluate((el: HTMLDetailsElement) => el.open)
  expect(isOpen).toBe(false)
  // click summary opens
  await det.locator('summary').click()
  await page.waitForTimeout(150)
  const isOpenAfter = await det.evaluate((el: HTMLDetailsElement) => el.open)
  expect(isOpenAfter).toBe(true)
})

test('OB.6 已知"暂不接"项 (block ref / note embed / inline tag) 不导致 404 / 不崩', async ({ page }) => {
  // these should render as plain text or wikilink-style anchors but not break the page
  const body = await page.locator('.markdown-body').first().innerText()
  expect(body).toContain('#demo-tag')        // tag still shown as text
  expect(body).toContain('some-note')         // block ref still shown
})
