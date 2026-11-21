import { expect, test, type APIRequestContext } from '@playwright/test'

const DASHBOARD = `http://127.0.0.1:${process.env.OMNI_E2E_DASHBOARD_PORT || '8200'}`
const PLAN_TAG = 'e2e/review_external_live_surface'
const EXTERNAL_LIVE_URL = 'https://192.0.2.10:12444/app/e2e-protected-surface/'

async function cleanup(request: APIRequestContext) {
  const response = await request.get(`${DASHBOARD}/api/boss-sight/reviewstage`)
  if (!response.ok()) return
  const { items } = await response.json()
  for (const material of items) {
    if (material.source_plan_id === PLAN_TAG) {
      await request.delete(`${DASHBOARD}/api/boss-sight/reviewstage/${material.id}`)
    }
  }
}

test.describe('review material external live-page carrier', () => {
  test.afterEach(async ({ request }) => cleanup(request))

  test('browser uses a new-window action instead of an iframe for a protected cross-origin page', async ({ page, request }) => {
    await cleanup(request)
    const created = await request.post(`${DASHBOARD}/api/boss-sight/reviewstage`, {
      data: {
        kind: 'html',
        tier: 'important',
        title: 'Protected external live page e2e',
        source_plan_id: PLAN_TAG,
        project: 'unfiled',
        track: 'runtime regression',
        version: 1,
        inline_content: '<!doctype html><html><body>fallback snapshot</body></html>',
        extra: { live_url: EXTERNAL_LIVE_URL },
      },
    })
    expect(created.ok(), `create material: ${created.status()} ${await created.text()}`).toBeTruthy()
    const material = await created.json()

    await page.addInitScript(() => {
      const originalClick = HTMLAnchorElement.prototype.click
      HTMLAnchorElement.prototype.click = function click() {
        if (this.dataset.omniNativeBrowser === '1') {
          ;(window as typeof window & { __lastExternalOpen?: string }).__lastExternalOpen = this.href
          return
        }
        originalClick.call(this)
      }
    })
    await page.goto(`${DASHBOARD}/?open_type=review_material&open_id=${material.id}`)

    await expect(page.getByTestId('web-page-external-fallback')).toBeVisible({ timeout: 15_000 })
    await expect(page.getByTestId('material-html')).toHaveCount(0)
    await page.getByRole('button', { name: '在新窗口打开' }).click()
    await expect.poll(() => page.evaluate(() => (
      window as typeof window & { __lastExternalOpen?: string }
    ).__lastExternalOpen)).toBe(EXTERNAL_LIVE_URL)
  })
})
