import { expect, test } from '@playwright/test'

const DASHBOARD = `http://127.0.0.1:${process.env.OMNI_E2E_FRONTEND_PORT || process.env.OMNI_E2E_DASHBOARD_PORT || '8200'}`

test.describe('Multiagent linked browser window', () => {
  test('pairs one viewer, routes selection there, and leaves a third window untouched', async ({ page, context }) => {
    await page.goto(`${DASHBOARD}/?open_type=multiagent&open_id=main`)
    await expect(page.getByTestId('multiagent-view')).toBeVisible({ timeout: 20_000 })
    await expect(page.getByTestId('multiagent-row').first()).toBeVisible({ timeout: 20_000 })

    const viewerPromise = context.waitForEvent('page')
    await page.getByTestId('multiagent-open-linked').click()
    const viewer = await viewerPromise
    await viewer.waitForLoadState('domcontentloaded')
    await expect(viewer.getByTestId('multiagent-view')).toBeVisible({ timeout: 20_000 })

    await expect(page.getByTestId('multiagent-link-state')).toHaveAttribute('data-connected', '1', { timeout: 15_000 })
    await expect(viewer.getByTestId('multiagent-link-state')).toHaveAttribute('data-connected', '1', { timeout: 15_000 })

    const linkedUrl = viewer.url()
    expect(new URL(linkedUrl).searchParams.get('ma_link')).toBeTruthy()

    const third = await context.newPage()
    await third.goto(linkedUrl)
    await expect(third.getByTestId('multiagent-view')).toBeVisible({ timeout: 20_000 })
    await expect(third.getByTestId('multiagent-link-state')).toHaveAttribute('data-connected', '0')

    const selectedRow = page.getByTestId('multiagent-row').first()
    const sessionId = await selectedRow.getAttribute('data-session-id')
    expect(sessionId).toBeTruthy()
    await selectedRow.locator('.ma-row-hit').click()

    await expect(viewer.locator(`[data-cc-session-id="${sessionId}"]`)).toBeVisible({ timeout: 20_000 })
    expect(new URL(viewer.url()).searchParams.get('open_id')).toBe(sessionId)

    await expect(third.getByTestId('multiagent-view')).toBeVisible()
    expect(new URL(third.url()).searchParams.get('open_id')).toBeNull()
    await expect(third.getByTestId('multiagent-link-state')).toHaveAttribute('data-connected', '0')
  })
})
