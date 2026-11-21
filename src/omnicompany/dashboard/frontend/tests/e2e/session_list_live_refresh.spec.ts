import { expect, test } from '@playwright/test'
import { setupErrorLogging } from './helpers'

test('threads surface discovers a PTY created outside the open page', async ({ page, request }) => {
  setupErrorLogging(page)
  await page.goto('/?surface=threads')
  await expect(page.getByTestId('surface-threads')).toBeVisible()
  await expect(page.getByText('加载中...')).toHaveCount(0, { timeout: 40_000 })

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
    await expect(
      page.getByTestId('session-id').filter({ hasText: session.id }),
    ).toBeVisible({ timeout: 20_000 })
  } finally {
    await request.delete(`/api/cc/sessions/${session.id}`).catch(() => undefined)
  }
})
