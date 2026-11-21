import type { Page } from '@playwright/test'

export const SHOTS = 'tests/screenshots'

export function setupErrorLogging(page: Page) {
  page.on('pageerror', (e) => console.error('[pageerror]', e.message))
  page.on('console', (m) => {
    if (m.type() === 'error') console.error('[console.error]', m.text())
  })
}

export async function gotoModule(page: Page, key: 'kb' | 'pm' | 'agent' | 'system' | 'settings'): Promise<void> {
  const titles: Record<string, string> = {
    kb: '知识库', pm: '项目', agent: 'Agent 会话', system: '系统', settings: '设置',
  }
  await page.locator(`button[title="${titles[key]}"]`).click()
}

/** Wait until the dashboard shell is fully mounted (activity bar present + ready to accept Cmd+K). */
export async function waitAppReady(page: Page): Promise<void> {
  // ActivityBar is the first thing rendered after app boot. Once present, kbar is registered.
  await page.locator('button[title]').first().waitFor({ timeout: 10000 })
  // give kbar's KBarProvider one tick to register its keyboard listener
  await page.waitForTimeout(150)
}

/** Open the kbar palette and run a query (robust under suite-wide load).
 *  Retries up to 3x if the dialog's first match doesn't surface (kbar's filtered list
 *  has its own debounce & the first dispatch can be eaten if React hasn't reconciled).
 */
export async function openCmdK(page: Page, query: string): Promise<void> {
  await waitAppReady(page)
  for (let attempt = 0; attempt < 3; attempt++) {
    await page.keyboard.press('Control+k')
    try {
      await page.getByRole('combobox').waitFor({ timeout: 3000 })
    } catch {
      continue  // hotkey didn't open; retry
    }
    await page.getByRole('combobox').fill(query)
    // wait until at least one filter result appears (kbar renders results as <div role="option">)
    try {
      await page.locator('[role="option"]').first().waitFor({ timeout: 2000 })
    } catch {
      // result didn't surface; close & retry
      await page.keyboard.press('Escape')
      continue
    }
    await page.waitForTimeout(150)
    await page.keyboard.press('Enter')
    return
  }
  throw new Error(`openCmdK: failed to open + dispatch query=${query!} after 3 attempts`)
}

/** Expand worker tree path to a leaf, skipping already-expanded top-level (domains/services/...). */
export async function expandWorkerPath(page: Page, leafId: string): Promise<void> {
  const parts = leafId.split('/').slice(0, -2)
  for (let i = 2; i <= parts.length; i++) {
    const path = parts.slice(0, i).join('/')
    const el = page.locator(`[data-tree="worker"] div[title="${path}"]`)
    await el.waitFor({ timeout: 5000 })
    await el.click()
  }
}
