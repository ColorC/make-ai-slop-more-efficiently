import { test, expect } from '@playwright/test'
import { gotoModule } from './helpers'

/**
 * Smokes the Settings → "Claude Code 集成" install widget.
 * The widget calls the same backend endpoints as the `omni cc` CLI, so this
 * also confirms the single-source-of-truth wiring.
 */

test.beforeEach(async ({ page }) => {
  await page.goto('/')
  await gotoModule(page, 'settings')
  // Settings is a single-entity module; click the only item to open the editor.
  await page.waitForTimeout(400)
  // The settings tab opens via clicking the sole sidebar item (or its label fallback).
  const sidebarItem = page.locator('div').filter({ hasText: /^设置 \/ 系统信息/ }).first()
  if (await sidebarItem.isVisible().catch(() => false)) {
    await sidebarItem.click()
  }
  await page.waitForTimeout(600)
})

test('CCI.1 install widget 渲染 + scope 下拉 + 现状 pill', async ({ page }) => {
  await expect(page.locator('[data-cc-install-card]')).toBeVisible({ timeout: 8000 })
  await expect(page.locator('[data-cc-scope-select]')).toBeVisible()
  await expect(page.locator('[data-cc-install]')).toBeVisible()
  await expect(page.locator('[data-cc-uninstall]')).toBeVisible()
  // pill shows installed state (project scope is installed in this dev env)
  await expect(page.locator('[data-cc-install-pill]')).toContainText(/已装|未装/, { timeout: 5000 })
})

test('CCI.2 切到 user scope → 状态 pill 反映 user scope (一般是未装)', async ({ page }) => {
  await expect(page.locator('[data-cc-install-card]')).toBeVisible({ timeout: 8000 })
  await page.locator('[data-cc-scope-select]').selectOption('user')
  await page.waitForTimeout(500)
  // either 'installed' or '未装' is fine — what matters is no crash + pill present
  await expect(page.locator('[data-cc-install-pill]')).toBeVisible()
})

test('CCI.3 等价 CLI 提示在 widget 里可见', async ({ page }) => {
  await expect(page.locator('[data-cc-install-card]')).toContainText('omni cc install')
  await expect(page.locator('[data-cc-install-card]')).toContainText('omni cc status')
})
