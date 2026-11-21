import { test, expect } from '@playwright/test'
import { SHOTS, setupErrorLogging, gotoModule } from './helpers'

test.beforeEach(async ({ page }) => {
  setupErrorLogging(page)
  await page.goto('/')
  await gotoModule(page, 'pm')
})

test('C1 PM module sidebar 树形显示 plan (按 category 分组)', async ({ page }) => {
  // PlanSidebar tree wrapper carries data-tree="plan"
  await expect(page.locator('[data-tree="plan"]')).toBeVisible({ timeout: 10000 })
  // top-level dirs (default-expanded): _infra, domain, _cross 之类
  await expect(page.locator('[data-tree="plan"] >> text=/_infra/').first()).toBeVisible({ timeout: 10000 })
  // WEB-FOUNDATION plan should appear under _infra
  await expect(page.locator('[data-tree="plan"] [data-plan-id*="WEB-FOUNDATION"]').first()).toBeVisible({ timeout: 10000 })
})

test('C2 点 plan 名 → 内联 expand 显 .md 文件清单 (不开中央 tab)', async ({ page }) => {
  const planRow = page.locator('[data-tree="plan"] [data-plan-id*="WEB-FOUNDATION"]').first()
  await planRow.click()
  await page.waitForTimeout(500)
  // file list shows up under the plan row
  await expect(page.locator('[data-tree="plan"] [data-plan-file="plan.md"]').first()).toBeVisible({ timeout: 5000 })
  // central area should NOT have opened a plan tab (we don't open central on dir click)
  // verify by counting dockview tabs: should be 0 (or the watermark visible)
  const tabs = page.locator('.dv-tab')
  const tabCount = await tabs.count()
  expect(tabCount).toBe(0)
})

test('C3 点 plan.md 文件 → KB 编辑器在中央打开 note tab', async ({ page }) => {
  const planRow = page.locator('[data-tree="plan"] [data-plan-id*="WEB-FOUNDATION"]').first()
  await planRow.click()
  await page.waitForTimeout(500)
  await page.locator('[data-tree="plan"] [data-plan-file="plan.md"]').first().click()
  await page.waitForTimeout(800)
  // note tab opens — verify by note's 编辑 mode buttons
  await expect(page.getByRole('button', { name: /编辑/ })).toBeVisible({ timeout: 10000 })
  await page.screenshot({ path: `${SHOTS}/pm_plan_open.png`, fullPage: true })
})

test('C4 过滤生效 (输 archive)', async ({ page }) => {
  await page.getByPlaceholder('过滤...').fill('archive')
  await page.waitForTimeout(400)
  // _archive 下的某条 plan 应该出现 (filter 强制全展开)
  await expect(page.locator('[data-tree="plan"] [data-plan-id*="_archive"]').first()).toBeVisible({ timeout: 5000 })
})
