import { test, expect } from '@playwright/test'
import { SHOTS, setupErrorLogging } from './helpers'

test.beforeEach(async ({ page }) => {
  setupErrorLogging(page)
  await page.goto('/')
})

test('B8.1 全文搜索 标签 可见 + 切换', async ({ page }) => {
  await expect(page.getByRole('button', { name: '全文搜索' })).toBeVisible()
  await page.getByRole('button', { name: '全文搜索' }).click()
  await expect(page.getByPlaceholder(/全文搜索 docs/)).toBeVisible()
})

test('B8.2 输入关键字 触发后端 _search → 命中显示', async ({ page }) => {
  await page.getByRole('button', { name: '全文搜索' }).click()
  await page.getByPlaceholder(/全文搜索 docs/).fill('ARCHITECTURE')
  await page.waitForTimeout(800)
  await expect(page.getByText(/\d+ 命中/)).toBeVisible()
  await expect(page.locator('mark').first()).toBeVisible({ timeout: 5000 })
  await page.screenshot({ path: `${SHOTS}/kb_search_hits.png`, fullPage: true })
})

test('B8.3 点击结果开 note tab', async ({ page }) => {
  await page.getByRole('button', { name: '全文搜索' }).click()
  await page.getByPlaceholder(/全文搜索 docs/).fill('ARCHITECTURE')
  await page.waitForTimeout(800)
  const firstHit = page.locator('div[title]').filter({ hasText: 'ARCHITECTURE' }).first()
  await firstHit.click()
  await expect(page.getByText(/反链 · /)).toBeVisible({ timeout: 5000 })
})

test('B8.4 空输入显示提示', async ({ page }) => {
  await page.getByRole('button', { name: '全文搜索' }).click()
  await expect(page.getByText('输入关键字开始搜索')).toBeVisible()
})

test('B8.5 无匹配显示无匹配', async ({ page }) => {
  await page.getByRole('button', { name: '全文搜索' }).click()
  await page.getByPlaceholder(/全文搜索 docs/).fill('zz_no_match_xx_qq')
  await page.waitForTimeout(800)
  await expect(page.getByText('无匹配')).toBeVisible({ timeout: 5000 })
})
