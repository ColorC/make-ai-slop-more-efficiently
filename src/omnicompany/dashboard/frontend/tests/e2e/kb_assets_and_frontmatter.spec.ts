import { test, expect } from '@playwright/test'
import { setupErrorLogging, openCmdK } from './helpers'

test.beforeEach(async ({ page }) => {
  setupErrorLogging(page)
  await page.goto('/')
  await openCmdK(page, 'kb_markdown_demo')
  await expect(page.locator('.markdown-body h1').first()).toBeVisible({ timeout: 15000 })
})

// ─── Frontmatter ──────────────────────────────────────────────────────────────

test('FM.1 frontmatter card 渲染在头部', async ({ page }) => {
  const card = page.locator('[data-frontmatter="1"]').first()
  await expect(card).toBeVisible()
  // contains text "frontmatter" label
  await expect(card).toContainText(/frontmatter/i)
})

test('FM.2 解析的 key/value 行可见', async ({ page }) => {
  const card = page.locator('[data-frontmatter="1"]').first()
  await expect(card.locator('[data-fm-key="title"]')).toBeVisible()
  await expect(card.locator('[data-fm-key="title"]')).toContainText(/KB Markdown 渲染特性 demo/)
  await expect(card.locator('[data-fm-key="status"]')).toContainText('live')
  await expect(card.locator('[data-fm-key="tags"]')).toBeVisible()
})

// ─── Image embed ──────────────────────────────────────────────────────────────

test('IMG.1 ![[image.png]] → <img> w/ asset src', async ({ page }) => {
  // scroll to bottom to render section 13
  await page.locator('.markdown-body').first().evaluate((el) => el.scrollTo(0, el.scrollHeight))
  await page.waitForTimeout(400)
  const img = page.locator('img[data-wiki-asset="demo_image.png"]').first()
  await expect(img).toBeVisible({ timeout: 5000 })
  const src = await img.getAttribute('src')
  expect(src).toMatch(/\/api\/notes\/[^/]+\/asset\/demo_image\.png$/)
})

test('IMG.2 资源真返回非 0 字节 (网络层)', async ({ page }) => {
  await page.locator('.markdown-body').first().evaluate((el) => el.scrollTo(0, el.scrollHeight))
  await page.waitForTimeout(400)
  const img = page.locator('img[data-wiki-asset="demo_image.png"]').first()
  // wait for natural load
  await img.evaluate((el: HTMLImageElement) =>
    new Promise<void>((resolve, reject) => {
      if (el.complete && el.naturalWidth > 0) return resolve()
      el.onload = () => resolve()
      el.onerror = () => reject(new Error('image failed to load'))
      setTimeout(() => reject(new Error('image load timeout')), 5000)
    })
  )
  const naturalW = await img.evaluate((el: HTMLImageElement) => el.naturalWidth)
  expect(naturalW).toBeGreaterThan(0)
})

test('IMG.3 alt 文字传过去', async ({ page }) => {
  await page.locator('.markdown-body').first().evaluate((el) => el.scrollTo(0, el.scrollHeight))
  await page.waitForTimeout(400)
  // there are TWO ![[demo_image.png...]] in the demo: one bare, one with |alt
  const imgs = page.locator('img[data-wiki-asset="demo_image.png"]')
  const count = await imgs.count()
  expect(count).toBeGreaterThanOrEqual(2)
  const altSecond = await imgs.nth(1).getAttribute('alt')
  expect(altSecond).toContain('测试图片 alt')
})
