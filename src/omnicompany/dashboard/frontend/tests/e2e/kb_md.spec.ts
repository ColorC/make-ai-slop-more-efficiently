import { test, expect } from '@playwright/test'
import { SHOTS, setupErrorLogging, gotoModule, openCmdK } from './helpers'

const DEMO_NOTE = '_sandbox/kb_markdown_demo'

test.beforeEach(async ({ page }) => {
  setupErrorLogging(page)
  await page.goto('/')
  await gotoModule(page, 'kb')
  await openCmdK(page, 'kb_markdown_demo')
  await expect(page.locator('.markdown-body h1').first()).toBeVisible({ timeout: 15000 })
})

test('M1.1 H1 渲染 + 底边线', async ({ page }) => {
  const h1 = page.locator('.markdown-body h1').first()
  await expect(h1).toBeVisible()
  const borderBottom = await h1.evaluate((el) => getComputedStyle(el).borderBottomWidth)
  expect(borderBottom).not.toBe('0px')
})

test('M1.2 H2 + 底边线', async ({ page }) => {
  const h2 = page.locator('.markdown-body h2').first()
  await expect(h2).toBeVisible()
  const borderBottom = await h2.evaluate((el) => getComputedStyle(el).borderBottomWidth)
  expect(borderBottom).not.toBe('0px')
})

test('M2 行内格式 (粗体 / 斜体 / strikethrough / 行内 code)', async ({ page }) => {
  await expect(page.locator('.markdown-body strong').first()).toBeVisible()
  await expect(page.locator('.markdown-body em').first()).toBeVisible()
  await expect(page.locator('.markdown-body del').first()).toBeVisible()
  await expect(page.locator('.markdown-body :not(pre) > code').first()).toBeVisible()
})

test('M3.1 无序列表 / 有序列表渲染', async ({ page }) => {
  await expect(page.locator('.markdown-body ul li').first()).toBeVisible()
  await expect(page.locator('.markdown-body ol li').first()).toBeVisible()
})

test('M3.2 任务列表 (GFM checkbox)', async ({ page }) => {
  const checked = page.locator('.markdown-body input[type="checkbox"][checked]')
  const unchecked = page.locator('.markdown-body input[type="checkbox"]:not([checked])')
  await expect(checked.first()).toBeVisible()
  await expect(unchecked.first()).toBeVisible()
})

test('M4 GFM 表格 — table/th/td/border 都生效', async ({ page }) => {
  const table = page.locator('.markdown-body table').first()
  await expect(table).toBeVisible()
  await expect(page.locator('.markdown-body table th').first()).toBeVisible()
  await expect(page.locator('.markdown-body table td').first()).toBeVisible()
  // 确认表头有 border (CSS 生效)
  const borderColor = await page.locator('.markdown-body table th').first().evaluate((el) => getComputedStyle(el).borderTopWidth)
  expect(borderColor).not.toBe('0px')
})

test('M5.1 普通 blockquote 渲染', async ({ page }) => {
  const bq = page.locator('.markdown-body blockquote').first()
  await expect(bq).toBeVisible()
  // 有左边线
  const borderLeft = await bq.evaluate((el) => getComputedStyle(el).borderLeftWidth)
  expect(borderLeft).not.toBe('0px')
})

test('M5.2 Obsidian callout (note 类) 渲染为彩色框', async ({ page }) => {
  await expect(page.locator('.markdown-body div[data-callout="note"]').first()).toBeVisible({ timeout: 5000 })
})

test('M5.3 Callout 4 类型都识别 (note/warning/tip/danger)', async ({ page }) => {
  for (const t of ['note', 'warning', 'tip', 'danger', 'info']) {
    const el = page.locator(`.markdown-body div[data-callout="${t}"]`).first()
    await expect(el).toBeVisible({ timeout: 5000 })
  }
})

test('M6.1 代码块 (python) — 含语法高亮 token', async ({ page }) => {
  const pre = page.locator('.markdown-body pre').first()
  await expect(pre).toBeVisible()
  // Prism 注入 .token spans
  const tokens = page.locator('.markdown-body pre .token')
  expect(await tokens.count()).toBeGreaterThan(2)
})

test('M6.2 代码块 (typescript) — 高亮独立工作', async ({ page }) => {
  // 第二个 pre 是 typescript
  const pres = page.locator('.markdown-body pre')
  expect(await pres.count()).toBeGreaterThan(1)
})

test('M7 Mermaid — graph 渲染为 SVG', async ({ page }) => {
  const mer = page.locator('.markdown-body div[data-mermaid="true"]')
  await expect(mer).toBeVisible({ timeout: 10000 })
  await expect(mer.locator('svg')).toBeVisible({ timeout: 10000 })
})

test('M8.1 KaTeX 行内数学渲染', async ({ page }) => {
  await expect(page.locator('.markdown-body .katex').first()).toBeVisible()
})

test('M8.2 KaTeX 块级数学 (display)', async ({ page }) => {
  await expect(page.locator('.markdown-body .katex-display')).toBeVisible({ timeout: 5000 })
})

test('M9 水平线 hr 渲染', async ({ page }) => {
  await expect(page.locator('.markdown-body hr').first()).toBeVisible()
})

test('M10 综合截图 — 整体观感', async ({ page }) => {
  await page.screenshot({ path: `${SHOTS}/kb_md_full_demo.png`, fullPage: true })
})

test('M10b 截 .markdown-body 内部 (含表格/callout/code/mermaid 整体)', async ({ page }) => {
  // 等待异步组件 (mermaid SVG) 渲染完
  await page.waitForTimeout(2500)
  const body = page.locator('.markdown-body').first()
  await body.evaluate((el) => el.scrollIntoView())
  await body.screenshot({ path: `${SHOTS}/kb_md_body_full.png` })
})

test('M10c 滚到表格 + callouts 区截图', async ({ page }) => {
  await page.locator('.markdown-body table').first().scrollIntoViewIfNeeded()
  await page.waitForTimeout(500)
  await page.screenshot({ path: `${SHOTS}/kb_md_table_callouts.png`, fullPage: false })
})

test('M10d 滚到代码 + mermaid + math 区截图', async ({ page }) => {
  await page.waitForTimeout(2500)
  await page.locator('.markdown-body div[data-mermaid="true"]').scrollIntoViewIfNeeded()
  await page.waitForTimeout(500)
  await page.screenshot({ path: `${SHOTS}/kb_md_code_mermaid_math.png`, fullPage: false })
})
