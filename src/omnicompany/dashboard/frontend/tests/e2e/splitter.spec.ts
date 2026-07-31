import { test, expect } from '@playwright/test'
import { setupErrorLogging } from './helpers'

test.beforeEach(async ({ page }) => {
  setupErrorLogging(page)
  await page.goto('/')
})

test('S1 sidebar 右边 splitter 拖拽 改宽度', async ({ page }) => {
  // sidebar default = 280, splitter at right edge
  // grab dimensions before / after
  const before = await page.locator('aside, div').filter({ hasText: /^系统$/ }).first().boundingBox()
  // The splitter is the 4px-wide div with cursor=ew-resize, after sidebar
  const splitters = page.locator('div[title="拖拽调整宽度"]')
  await expect(splitters.first()).toBeVisible()
  const sBox = await splitters.first().boundingBox()
  if (!sBox) throw new Error('splitter not found')
  // drag right by 80px
  await page.mouse.move(sBox.x + 2, sBox.y + 10)
  await page.mouse.down()
  await page.mouse.move(sBox.x + 80, sBox.y + 10, { steps: 10 })
  await page.mouse.up()
  await page.waitForTimeout(200)
  // visually confirm — sidebar got wider via DOM check (approximate)
  const sBox2 = await splitters.first().boundingBox()
  if (sBox2 && sBox.x) expect(sBox2.x).toBeGreaterThan(sBox.x + 30)
})

test('S2 bottom panel splitter 拖拽 改高度', async ({ page }) => {
  const splitters = page.locator('div[title="拖拽调整高度"]')
  await expect(splitters.first()).toBeVisible()
  const sBox = await splitters.first().boundingBox()
  if (!sBox) throw new Error('splitter not found')
  await page.mouse.move(sBox.x + 100, sBox.y + 2)
  await page.mouse.down()
  await page.mouse.move(sBox.x + 100, sBox.y - 60, { steps: 10 })
  await page.mouse.up()
  await page.waitForTimeout(200)
  const sBox2 = await splitters.first().boundingBox()
  if (sBox2 && sBox.y) expect(sBox2.y).toBeLessThan(sBox.y - 30)
})
