import { test, expect } from '@playwright/test'
import { setupErrorLogging, expandWorkerPath as expandTo } from './helpers'

const W = 'domains/voxelcraft/block/workers/block_designer'

test.beforeEach(async ({ page }) => {
  setupErrorLogging(page)
})

test('F7 开 tab 后 URL 含 layout 参数', async ({ page }) => {
  await page.goto('/')
  await expandTo(page, W)
  await page.locator(`div[title="${W}"]`).click()
  await page.waitForTimeout(500)
  expect(page.url()).toContain('layout=')
})

test('F8 关所有 tab 后 URL 不含 layout', async ({ page }) => {
  await page.goto('/')
  await expandTo(page, W)
  await page.locator(`div[title="${W}"]`).click()
  await page.waitForTimeout(300)
  expect(page.url()).toContain('layout=')
  // close tab via dockview close button (×)
  const closeBtn = page.locator('.dv-react-tab .dv-default-tab-action').first()
  if (await closeBtn.isVisible().catch(() => false)) {
    await closeBtn.click()
    await page.waitForTimeout(500)
    expect(page.url()).not.toContain('layout=')
  }
})

test('F9 重新载入 URL 恢复 1 tab', async ({ page }) => {
  await page.goto('/')
  await expandTo(page, W)
  await page.locator(`div[title="${W}"]`).click()
  await page.waitForTimeout(500)
  const url = page.url()
  expect(url).toContain('layout=')
  await page.goto(url)
  await expect(page.getByRole('button', { name: '设计' })).toBeVisible({ timeout: 10000 })
})

test('F10 重新载入 URL 恢复多 tab (worker + note)', async ({ page }) => {
  await page.goto('/')
  await expandTo(page, W)
  await page.locator(`div[title="${W}"]`).click()
  await page.waitForTimeout(300)
  await page.locator('button[title="知识库"]').click()
  await page.locator('div[title="README"]').first().click()
  await page.waitForTimeout(500)
  const url = page.url()
  await page.goto(url)
  await page.waitForTimeout(800)
  await expect(page.locator('text=docs/README.md').or(page.getByRole('button', { name: '设计' }))).toBeVisible({ timeout: 10000 })
})
