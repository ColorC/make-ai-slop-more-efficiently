import { test, expect } from '@playwright/test'
import { SHOTS, setupErrorLogging, openCmdK } from './helpers'

const DEMO = '_sandbox/kb_markdown_demo'

test.beforeEach(async ({ page }) => {
  setupErrorLogging(page)
  await page.goto('/')
  await openCmdK(page, 'kb_markdown_demo')
  await expect(page.locator('.markdown-body h1').first()).toBeVisible({ timeout: 15000 })
  await page.locator('.markdown-body').first().evaluate((el) => el.scrollTo(0, el.scrollHeight))
  await page.waitForTimeout(400)
})

test('OL.1 omni://worker 协议解析为带 data-entity-type=worker 的 anchor', async ({ page }) => {
  const worker = page.locator('a.wikilink-worker').first()
  await expect(worker).toBeVisible({ timeout: 10000 })
  const dataType = await worker.getAttribute('data-entity-type')
  expect(dataType).toBe('worker')
})

test('OL.2 omni://trace 协议解析', async ({ page }) => {
  const trace = page.locator('a.wikilink-trace').first()
  await expect(trace).toBeVisible({ timeout: 10000 })
  expect(await trace.getAttribute('data-entity-type')).toBe('trace')
})

test('OL.3 omni://session 协议解析 + alias 显示', async ({ page }) => {
  const sess = page.locator('a.wikilink-session').first()
  await expect(sess).toBeVisible({ timeout: 10000 })
  expect(await sess.getAttribute('data-entity-type')).toBe('session')
  await expect(sess).toHaveText('示例会话')
})

test('OL.3b 短写 plan:xxx 解析为 plan 类型', async ({ page }) => {
  const plan = page.locator('a.wikilink-plan').first()
  await expect(plan).toBeVisible({ timeout: 10000 })
  expect(await plan.getAttribute('data-entity-type')).toBe('plan')
})

test('OL.4 短写 worker:xxx 协议等价', async ({ page }) => {
  // multiple worker wikilinks exist (full omni:// + short worker:); just count >= 2
  const count = await page.locator('a.wikilink-worker').count()
  expect(count).toBeGreaterThanOrEqual(2)
})

test('OL.5 无前缀 [[README]] 默认 note 类型', async ({ page }) => {
  const note = page.locator('a.wikilink-note').first()
  await expect(note).toBeVisible()
  expect(await note.getAttribute('data-entity-type')).toBe('note')
})

test('OL.6 点 worker wikilink → 中央开 worker tab', async ({ page }) => {
  const worker = page.locator('a.wikilink-worker').first()
  await worker.click()
  await page.waitForTimeout(800)
  // worker tab opens with 设计/运行/历史 facet buttons
  await expect(page.getByRole('button', { name: '设计' })).toBeVisible({ timeout: 10000 })
})

test('OL.7 hover wikilink 弹 HoverCard 预览', async ({ page }) => {
  const worker = page.locator('a.wikilink-worker').first()
  await worker.scrollIntoViewIfNeeded()
  await worker.hover()
  // delay 400ms before show + fetch
  await page.waitForTimeout(1500)
  // HoverCard 显 type 名 + title
  const card = page.locator('div').filter({ hasText: /^worker$/i }).first()
  await expect(card).toBeVisible({ timeout: 5000 })
  await page.screenshot({ path: `${SHOTS}/kb_omni_hover.png`, fullPage: false })
})
