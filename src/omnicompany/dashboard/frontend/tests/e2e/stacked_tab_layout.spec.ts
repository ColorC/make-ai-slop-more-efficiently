import { expect, test } from '@playwright/test'

test.use({ viewport: { width: 1600, height: 800 }, deviceScaleFactor: 1.25 })

test('stacked rows follow tab changes and sidecar width in the same layout', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('omni:tabLayout', 'stacked')
    const tabs = Array.from({ length: 9 }, (_, i) => ({
      id: `settings:main#layout-${i}`,
      ref: { type: 'settings', id: 'main' },
      facet: `layout-${i}`,
      title: `页签布局检查 ${i}`,
    }))
    sessionStorage.setItem('omni.cockpit.tabSnapshot.v4', JSON.stringify({ tabs, activeId: tabs[0].id }))
  })
  await page.goto('/')
  await expect(page.locator('.dv-tab')).toHaveCount(9)

  // Record every frame, including the frame where Dockview catches up with a
  // changed sidecar width. Waiting for a settled height hides the regression.
  await page.evaluate(() => {
    const state = { running: true, errors: [] as string[], heights: [] as number[] }
    ;(window as any).__stackedLayoutCheck = state
    const sample = () => {
      if (!state.running) return
      for (const bar of document.querySelectorAll<HTMLElement>('.dv-tabs-and-actions-container')) {
        const list = bar.querySelector<HTMLElement>('.dv-tabs-container')!
        const viewport = bar.querySelector<HTMLElement>('.dv-scrollable')!
        const listRect = list.getBoundingClientRect()
        if (!listRect.width) continue
        const tabs = [...list.querySelectorAll<HTMLElement>(':scope > .dv-tab')]
        const bottom = Math.max(28, ...tabs.map(tab => tab.getBoundingClientRect().bottom - listRect.top))
        const expected = Math.min(112, bottom) + 4
        const height = bar.getBoundingClientRect().height
        if (!state.heights.includes(height)) state.heights.push(height)
        if (Math.abs(height - expected) > 0.5 || Math.abs(viewport.clientHeight - Math.min(112, bottom)) > 0.5) {
          if (state.errors.length < 20) state.errors.push(`bar=${height}, expected=${expected}, width=${listRect.width}, tabs=${tabs.length}`)
        }
      }
      requestAnimationFrame(sample)
    }
    requestAnimationFrame(sample)
  })

  for (let cycle = 0; cycle < 3; cycle++) {
    await page.getByRole('button', { name: '设置', exact: true }).first().click()
    await expect(page.locator('.dv-tab')).toHaveCount(10)
    for (const width of [280, 1400, 380]) {
      await page.locator('.tab-sidecar-main').evaluate((main, width) => {
        (main as HTMLElement).style.flex = `0 0 ${width}px`
      }, width)
      await page.locator('.dv-tab').nth(cycle + 1).click()
      await page.evaluate(() => new Promise<void>(resolve => {
        requestAnimationFrame(() => requestAnimationFrame(() => resolve()))
      }))
    }
    await page.locator('[data-omni-tab-id="settings:main"] .dv-default-tab-action').click({ force: true })
    await expect(page.locator('.dv-tab')).toHaveCount(9)
  }

  await page.locator('.tab-sidecar-main').evaluate(main => { (main as HTMLElement).style.display = 'none' })
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => resolve())))
  await page.locator('.tab-sidecar-main').evaluate(main => { (main as HTMLElement).style.removeProperty('display') })

  await page.locator('.tab-sidecar-main').evaluate(main => { (main as HTMLElement).style.flex = '0 0 200px' })
  await page.evaluate(() => new Promise<void>(resolve => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()))
  }))
  const overflow = await page.locator('.dv-scrollable').first().evaluate(viewport => {
    viewport.scrollTop = viewport.scrollHeight
    return { height: viewport.clientHeight, scrollTop: viewport.scrollTop }
  })
  expect(overflow.height).toBe(112)
  expect(overflow.scrollTop).toBeGreaterThan(0)

  const result = await page.evaluate(() => {
    const state = (window as any).__stackedLayoutCheck
    state.running = false
    return { errors: state.errors, heights: state.heights }
  })
  expect(result.errors).toEqual([])
  expect(result.heights).toContain(32)
  expect(result.heights).toContain(116)
})
