import { expect, test } from '@playwright/test'
import { setupErrorLogging } from './helpers'

const dashboardApi = `http://127.0.0.1:${process.env.OMNI_E2E_DASHBOARD_PORT || '8200'}`

// 2026-11-16: 概览、Todo 两页移出侧栏，只留会话、画布；默认页是会话。之后 Studio 改为侧栏第三页。
test('会话右栏有会话、画布、Studio 三页，默认停在会话页', async ({ page, request }) => {
  setupErrorLogging(page)
  await page.setViewportSize({ width: 780, height: 820 })
  const response = await request.post(`${dashboardApi}/api/cc/sessions`, {
    data: {
      cmd: ['powershell.exe', '-NoLogo', '-NoProfile', '-Command', 'Start-Sleep -Seconds 120'],
      cwd: 'C:/workspace/',
      cols: 100,
      rows: 30,
    },
  })
  expect(response.ok()).toBeTruthy()
  const session = await response.json() as { id: string }

  try {
    await page.goto(`/?open_type=cc_session&open_id=${session.id}&open_title=会话侧栏验收`)
    await expect(page.locator(`[data-cc-session-id="${session.id}"]`)).toBeVisible({ timeout: 15_000 })
    const companion = page.getByTestId('session-companion')
    await expect(companion.getByTestId('companion-nav-multiagent')).toHaveText('会话')
    await expect(companion.getByTestId('companion-nav-notes')).toHaveText('画布')
    await expect(companion.getByTestId('companion-nav-studio')).toHaveText('Studio')
    await expect(companion.locator('.cccp-nav-pages [data-testid^="companion-nav-"]')).toHaveCount(3)
    await expect(companion.getByTestId('companion-nav-overview')).toHaveCount(0)
    await expect(companion).toHaveAttribute('data-page', 'multiagent', { timeout: 10_000 })
    await expect(companion.getByTestId('companion-multiagent')).toBeVisible()
  } finally {
    await request.delete(`${dashboardApi}/api/cc/sessions/${session.id}`).catch(() => undefined)
  }
})
