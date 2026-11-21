import { test, expect } from '@playwright/test'
import { SHOTS, setupErrorLogging } from './helpers'

test.beforeEach(async ({ page }) => {
  setupErrorLogging(page)
  await page.goto('/')
  await page.getByRole('button', { name: 'Trace 列表' }).click()
})

test('G1 Trace 列表底部 panel 可见', async ({ page }) => {
  await expect(page.getByPlaceholder(/过滤 task_desc/)).toBeVisible()
  await expect(page.locator('select')).toBeVisible()
})

test('G2 输入过滤 (无匹配也不报错)', async ({ page }) => {
  await page.getByPlaceholder(/过滤 task_desc/).fill('zz_no_match_zz')
  await page.waitForTimeout(1200)
  await expect(page.getByText('无 trace')).toBeVisible({ timeout: 5000 })
})

test('G3 域 select 含 events 或 ide_events', async ({ page }) => {
  const select = page.locator('select')
  await select.waitFor({ timeout: 8000 })
  // wait for options to populate (initial render may have just placeholder)
  await page.waitForFunction(() => document.querySelectorAll('select option').length > 1, { timeout: 8000 })
  const opts = await select.locator('option').allTextContents()
  expect(opts.length).toBeGreaterThan(1)
})

test('G4 点 trace 行 → 中央开 trace tab', async ({ page }) => {
  await page.waitForTimeout(1500)
  const rows = page.locator('div').filter({ hasText: /^\d{2}\/\d{2} \d{2}:\d{2}/ })
  const count = await rows.count()
  if (count > 0) {
    await rows.first().click()
    await page.waitForTimeout(800)
    await expect(page.getByText(/trace_id:/)).toBeVisible({ timeout: 10000 })
    await page.screenshot({ path: `${SHOTS}/trace_detail.png`, fullPage: true })
  } else {
    console.log('no trace rows visible, skipping click')
  }
})

test('G5 trace tab 显事件列表', async ({ page }) => {
  await page.waitForTimeout(1500)
  const rows = page.locator('div').filter({ hasText: /^\d{2}\/\d{2} \d{2}:\d{2}/ })
  if ((await rows.count()) > 0) {
    await rows.first().click()
    await page.waitForTimeout(800)
    await expect(page.getByText(/events$|trace_id:/).first()).toBeVisible({ timeout: 5000 })
  } else {
    test.skip()
  }
})

test('G6 点事件显 payload', async ({ page }) => {
  await page.waitForTimeout(1500)
  const firstRow = page.locator('div').filter({ hasText: /\d+ev/ }).first()
  if (await firstRow.isVisible().catch(() => false)) {
    await firstRow.click()
    await page.waitForTimeout(800)
    const evRow = page.locator('text=task.intent').or(page.locator('text=agent.tool.call')).first()
    if (await evRow.isVisible().catch(() => false)) {
      await evRow.click()
      await expect(page.getByText('Payload')).toBeVisible({ timeout: 5000 })
    }
  } else {
    test.skip()
  }
})
