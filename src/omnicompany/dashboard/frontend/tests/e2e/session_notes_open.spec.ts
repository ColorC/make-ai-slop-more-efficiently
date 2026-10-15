import { expect, test } from '@playwright/test'
import { setupErrorLogging } from './helpers'

test('session 札记 button opens sidecar notes-web with height', async ({ page, request }) => {
  setupErrorLogging(page)
  const response = await request.post('/api/cc/sessions', {
    data: {
      cmd: ['powershell.exe', '-NoLogo', '-NoProfile', '-Command', 'Start-Sleep -Seconds 90'],
      cwd: 'C:/workspace/',
      cols: 100,
      rows: 30,
    },
  })
  expect(response.ok()).toBeTruthy()
  const session = await response.json() as { id: string }

  try {
    await page.setViewportSize({ width: 1600, height: 900 })
    await page.goto(`/?open_type=cc_session&open_id=${session.id}&open_title=札记打开验收`)
    await expect(page.locator(`[data-cc-session-id="${session.id}"]`)).toBeVisible({ timeout: 20_000 })
    await expect(page.getByTestId('session-open-notes')).toBeVisible()
    await expect(page.getByTestId('session-companion')).toHaveCount(0)

    await page.getByTestId('session-open-notes').click()
    await expect(page.getByTestId('session-companion')).toBeVisible({ timeout: 10_000 })
    await expect(page.getByTestId('session-companion')).toHaveAttribute('data-page', 'notes')
    await expect(page.getByTestId('companion-nav-notes')).toHaveText(/札记/)
    const host = page.getByTestId('companion-notes-host')
    await expect(host).toBeVisible()
    await expect.poll(async () => {
      const box = await host.boundingBox()
      return box?.height ?? 0
    }).toBeGreaterThan(200)

    const iframe = host.locator('iframe')
    await expect(iframe).toBeVisible()
    const frame = page.frameLocator('[data-testid="companion-notes-host"] iframe')
    await expect(frame.locator('[data-material-canvas="ready"]')).toBeVisible({ timeout: 20_000 })
  } finally {
    await request.delete(`/api/cc/sessions/${session.id}`).catch(() => undefined)
  }
})
