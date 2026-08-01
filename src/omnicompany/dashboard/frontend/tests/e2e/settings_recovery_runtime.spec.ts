import { expect, test } from '@playwright/test'

test('the production entry loads the recovered Settings and Token statistics surface', async ({ page }) => {
  await page.goto(`/?settings_recovery=${Date.now()}`)
  await page.getByTestId('cockpit-nav-settings').click()

  const settings = page.getByTestId('settings-page')
  await expect(settings).toBeVisible({ timeout: 15_000 })
  await expect(page.getByRole('radio', { name: '系统信息', exact: true })).toBeVisible()

  const tokenTab = page.getByRole('radio', { name: 'Token 统计', exact: true })
  // Other production E2E cases create screenshots/materials below dashboard/;
  // a --reload runtime can refresh this page once while the suite is active.
  await expect(async () => {
    await tokenTab.click()
    await expect(tokenTab).toHaveAttribute('aria-checked', 'true')
    await expect(page.getByText('累计 Token', { exact: true })).toBeVisible()
  }).toPass({ timeout: 30_000 })
  await expect(page.getByText('会话数', { exact: true })).toBeVisible()

  const entryScript = await page.locator('script[type="module"]').getAttribute('src')
  expect(entryScript).toMatch(/^\/assets\/index-[^?]+\.js$/)
})

test('the global command palette mounts once from the canonical ESM entry', async ({ page }) => {
  await page.goto(`/?command_palette_singleton=${Date.now()}`)
  await page.getByRole('button', { name: /搜索 \/ 命令/ }).click()

  await expect(page.getByRole('combobox', { expanded: true })).toHaveCount(1)
  await expect(page.getByRole('listbox')).toHaveCount(1)
})
