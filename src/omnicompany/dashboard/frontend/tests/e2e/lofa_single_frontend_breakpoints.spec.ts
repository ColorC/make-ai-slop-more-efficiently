import { expect, test, type Page } from '@playwright/test'

const viewports = [
  { name: 'phone-390x844', width: 390, height: 844 },
  { name: 'tablet-834x1194', width: 834, height: 1194 },
  { name: 'desktop-1440x960', width: 1440, height: 960 },
] as const

const surfaces = [
  { type: 'project_board', id: 'main', selector: '[data-testid="project-board"]' },
  { type: 'multiagent', id: 'main', selector: '[data-testid="multiagent-view"]' },
  { type: 'review_queue', id: 'main', selector: '[data-testid="review-board"]' },
] as const

async function expectNoPageOverflow(page: Page) {
  await expect.poll(() => page.evaluate(() => ({
    viewport: window.innerWidth,
    document: document.documentElement.scrollWidth,
  }))).toEqual(expect.objectContaining({
    viewport: page.viewportSize()!.width,
    document: expect.any(Number),
  }))
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  expect(overflow).toBeLessThanOrEqual(1)
}

for (const viewport of viewports) {
  test(`${viewport.name} keeps project, PTY/session and review surfaces usable`, async ({ page }) => {
    await page.setViewportSize(viewport)
    const errors: string[] = []
    page.on('pageerror', (error) => errors.push(String(error.message || error)))

    for (const surface of surfaces) {
      await page.goto(`/?open_type=${surface.type}&open_id=${surface.id}`)
      await expect(page.getByTestId('cockpit-shell')).toBeVisible({ timeout: 20_000 })
      await expect(page.locator(surface.selector)).toBeVisible({ timeout: 20_000 })
      if (surface.type === 'multiagent') {
        const providerPicker = page.getByTestId('multiagent-new-provider')
        await expect(providerPicker).toContainText('Codex CLI', { timeout: 15_000 })
        await providerPicker.click()
        await expect(page.getByRole('option').first()).toContainText('Codex CLI')
        await expect(page.getByRole('option', { name: 'Grok Build CLI' })).toBeVisible()
        await expect(page.getByRole('option')).toHaveCount(16)
        await page.keyboard.press('Escape')

        const modelPicker = page.getByTestId('multiagent-new-model')
        await expect(modelPicker).toContainText('CLI 默认')
        await expect(page.getByTestId('multiagent-new-effort')).toHaveCount(0)
        await modelPicker.click()
        await expect(page.getByRole('option', { name: 'GPT-5.6 Terra' })).toBeVisible()
        await expect(page.getByRole('option')).toHaveCount(4)
        await page.getByRole('option', { name: 'GPT-5.6 Terra' }).click()
        await expect(page.getByTestId('multiagent-new-effort')).toContainText('medium')
        await page.getByTestId('multiagent-new-effort').click()
        await expect(page.getByRole('option', { name: 'ultra' })).toBeVisible()
        await expect(page.getByRole('option')).toHaveCount(6)
        await page.keyboard.press('Escape')
      }
      await expectNoPageOverflow(page)
    }

    expect(errors).toEqual([])
  })
}
