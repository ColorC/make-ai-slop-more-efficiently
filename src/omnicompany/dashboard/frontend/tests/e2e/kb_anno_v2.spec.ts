import { test, expect } from '@playwright/test'
import { setupErrorLogging, openCmdK } from './helpers'

/**
 * S19: annotation extension to all block-level elements
 * (heading, list-item, table, blockquote, plain code block).
 * Per-element AnnotatedParagraph wrapper is keyed by `data-anno-tag`.
 */

test.beforeEach(async ({ page }) => {
  setupErrorLogging(page)
  await page.goto('/')
  await openCmdK(page, 'kb_markdown_demo')
  await expect(page.locator('.markdown-body h1').first()).toBeVisible({ timeout: 15000 })
  // Scroll to section 14 (annotation extension demo)
  await page.locator('.markdown-body').first().evaluate((el) => el.scrollTo(0, el.scrollHeight))
  await page.waitForTimeout(400)
})

test('S19.1 heading 也包了 annotation wrapper', async ({ page }) => {
  // every h1-h6 in markdown-body should now have data-anno-tag
  const headingsWithWrapper = page.locator('.markdown-body [data-anno-tag="h2"]')
  const count = await headingsWithWrapper.count()
  expect(count).toBeGreaterThan(0)
})

test('S19.2 list item (li) 单独可批', async ({ page }) => {
  const lis = page.locator('.markdown-body [data-anno-tag="li"]')
  const count = await lis.count()
  expect(count).toBeGreaterThan(2)
})

test('S19.3 table 整体可批 (一个 wrapper)', async ({ page }) => {
  const tables = page.locator('.markdown-body [data-anno-tag="table"]')
  const count = await tables.count()
  expect(count).toBeGreaterThanOrEqual(1)
})

test('S19.4 blockquote 整体可批', async ({ page }) => {
  const bq = page.locator('.markdown-body [data-anno-tag="blockquote"]')
  const count = await bq.count()
  expect(count).toBeGreaterThanOrEqual(1)
})

test('S19.5 无 language code 块 (pre) 可批', async ({ page }) => {
  const pre = page.locator('.markdown-body [data-anno-tag="pre"]')
  const count = await pre.count()
  expect(count).toBeGreaterThanOrEqual(1)
})

test('S19.6 hover heading → + 按钮出现', async ({ page }) => {
  const h2 = page.locator('.markdown-body [data-anno-tag="h2"]').first()
  await h2.scrollIntoViewIfNeeded()
  // hover the wrapper (parent of h2) to reveal +
  const wrapper = h2.locator('..')
  await wrapper.hover()
  await page.waitForTimeout(150)
  // The + button uses title='添加批注'
  const addBtn = wrapper.locator('span[title="添加批注"]')
  await expect(addBtn).toBeVisible()
})

test('S19.7 hover li → + 按钮出现', async ({ page }) => {
  const li = page.locator('.markdown-body [data-anno-tag="li"]').first()
  await li.scrollIntoViewIfNeeded()
  const wrapper = li.locator('..')
  await wrapper.hover()
  await page.waitForTimeout(150)
  const addBtn = wrapper.locator('span[title="添加批注"]')
  await expect(addBtn).toBeVisible()
})

test('S19.8 hr 不被 wrapper 包 (本身仍渲染但无 anno)', async ({ page }) => {
  // markdown-body 内有 <hr> 但不应有 data-anno-tag="hr"
  const hr = page.locator('.markdown-body hr').first()
  await expect(hr).toBeVisible()
  const annoHr = page.locator('.markdown-body [data-anno-tag="hr"]')
  expect(await annoHr.count()).toBe(0)
})
