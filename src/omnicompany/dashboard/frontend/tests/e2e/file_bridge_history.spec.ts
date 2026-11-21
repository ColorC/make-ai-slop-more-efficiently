import { expect, test } from '@playwright/test'

const DASHBOARD = `http://127.0.0.1:${process.env.OMNI_E2E_FRONTEND_PORT || process.env.OMNI_E2E_DASHBOARD_PORT || '8200'}`

test.describe('FileBridge recent uploads', () => {
  test.use({ permissions: ['clipboard-read', 'clipboard-write'] })

  test('opens in Dockview, reads the upload ledger, and copies an absolute path', async ({ page }) => {
    await page.goto(`${DASHBOARD}/?open_type=file_bridge&open_id=main`)
    await expect(page.getByTestId('file-bridge-page')).toBeVisible({ timeout: 20_000 })

    const row = page.locator('.fb-history-row').first()
    await expect(row).toBeVisible({ timeout: 20_000 })
    const expectedPath = (await row.locator('code').textContent())?.trim()
    expect(expectedPath).toMatch(/^[A-Za-z]:\\/)

    await row.click()
    await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe(expectedPath)
  })
})
