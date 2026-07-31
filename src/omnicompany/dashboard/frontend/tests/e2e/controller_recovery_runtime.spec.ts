import { expect, test } from '@playwright/test'

test('production Controller exposes native chat and session/CLI views', async ({ page }) => {
  await page.goto(`/?controller_recovery=${Date.now()}`)
  await page.getByTestId('cockpit-nav-controller').click()

  await expect(page.getByTestId('boss-controller-root')).toBeVisible({ timeout: 20_000 })
  await expect(page.getByTestId('controller-view-chat')).toBeVisible()
  await expect(page.getByTestId('controller-view-sessions')).toBeVisible()
  await expect(page.getByTestId('controller-view-multiagent')).toHaveCount(0)

  await page.getByTestId('controller-view-sessions').click()
  await expect(page.getByTestId('controller-sessions')).toBeVisible({ timeout: 20_000 })
  await expect(page.getByPlaceholder(/搜索标题/)).toBeVisible({ timeout: 20_000 })
  await expect(page.getByTestId('thread-session-search-count')).not.toHaveText('0 个会话', { timeout: 30_000 })
})
