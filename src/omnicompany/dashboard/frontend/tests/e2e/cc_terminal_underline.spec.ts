import { expect, test, request as apiRequest, type Page, type WebSocketRoute } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { randomUUID } from 'node:crypto'

const SESSION = 'terminal-underline-fixture'
const TITLE = '终端下划线回归'

async function terminalText(page: Page): Promise<string> {
  return page.evaluate((id) => {
    const inspect = (window as Window & {
      __OMNI_CLIENT_INSPECT__?: { info: () => { terminals: Array<{ sessionId: string; lines: string[] }> } }
    }).__OMNI_CLIENT_INSPECT__
    return inspect?.info().terminals.find((terminal) => terminal.sessionId === id)?.lines.join('\n') || ''
  }, SESSION)
}

test.beforeEach(async ({ page, context }, testInfo) => {
  if (process.env.OMNI_ANSI_REMOTE === '1') {
    const credentials = readFileSync(join(homedir(), 'codeweb', 'omni-access-credentials.txt'), 'utf8')
    const username = credentials.match(/Username:\s*(\S+)/)?.[1]
    const password = credentials.match(/Password:\s*(\S+)/)?.[1]
    if (!username || !password) throw new Error('Gateway credentials unavailable')
    // Bootstrap outside the traced page/request context so credentials are never in the trace.
    const bootstrap = await apiRequest.newContext({ baseURL: 'http://192.0.2.10:8210' })
    try {
      const response = await bootstrap.post('/api/desktop/session', {
        headers: {
          Authorization: `Basic ${Buffer.from(`${username}:${password}`).toString('base64')}`,
          'X-Omnicompany-Desktop': '1',
        },
        data: { instance_id: randomUUID().replaceAll('-', '') },
      })
      expect(response.status()).toBe(200)
      await context.addCookies((await bootstrap.storageState()).cookies)
    } finally {
      await bootstrap.dispose()
    }
  }
  if (testInfo.title.startsWith('reported session:')) return
  // A browser-only fixture exercises the actual Editor/xterm pipeline without touching a live PTY.
  await page.route(/\/api\/cc\/sessions(?:\?.*)?$/, (route) => route.fulfill({
    json: {
      items: [{ id: SESSION, cmd: ['shell'], cwd: 'C:/workspace/omnicompany',
        cols: 100, rows: 30, alive: true, provider: 'shell', display_title: TITLE, status: 'alive' }],
      alive_count: 1, recoverable: [], recoverable_count: 0,
    },
  }))
})

for (const source of ['output', 'snapshot'] as const) {
  test(`${source}: prompt control codes stay invisible before and after switching tabs`, async ({ page }, testInfo) => {
    let socket: WebSocketRoute | undefined
    const sample = '\x1b[4m(\x1b[24mok)\r\n\x1b[21mUNDERLINE_OFF\x1b[24m\r\n'
    await page.routeWebSocket(`**/api/cc/sessions/${SESSION}/ws**`, (route) => {
      socket = route
      route.onMessage((message) => {
        if (typeof message !== 'string' || JSON.parse(message).type !== 'attach') return
        route.send(JSON.stringify({ type: 'snapshot_begin', meta: { cols: 100, rows: 30, last_seq: 0, provider: 'shell' } }))
        route.send(JSON.stringify({ type: 'snapshot_chunk', chunks: [source === 'snapshot' ? sample : 'READY\r\n'] }))
        route.send(JSON.stringify({ type: 'snapshot_end' }))
      })
    })
    await page.goto(`/?${new URLSearchParams({ open_type: 'cc_session', open_id: SESSION, open_title: TITLE })}`)
    const terminal = page.locator(`[data-cc-session-id="${SESSION}"]`)
    await expect(terminal.locator('.xterm')).toBeVisible()
    await expect(terminal).toHaveAttribute('data-cc-session-connected', 'true')
    await expect(terminal.getByTestId('cc-terminal-restoring')).toHaveCount(0)
    if (source === 'output') socket!.send(JSON.stringify({ type: 'output', seq: 1, data: sample }))
    await expect.poll(() => terminalText(page)).toContain('UNDERLINE_OFF')
    await page.screenshot({ path: testInfo.outputPath('before-switch.png'), fullPage: true })
    expect(await terminalText(page)).toContain('(ok)')
    expect(await terminalText(page)).not.toContain('\\x1b[')

    const sessionTab = page.locator('.dv-tab').filter({ hasText: TITLE })
    const otherTab = page.locator('.dv-tab').filter({ hasNotText: TITLE }).first()
    await otherTab.click()
    await expect(terminal.locator('.xterm')).toHaveCount(0)
    await sessionTab.click()
    await expect(terminal.locator('.xterm')).toBeVisible()
    await expect(terminal.getByTestId('cc-terminal-restoring')).toHaveCount(0)
    if (source === 'output') socket!.send(JSON.stringify({ type: 'output', seq: 1, data: sample }))
    await expect.poll(() => terminalText(page)).toContain('UNDERLINE_OFF')
    expect(await terminalText(page)).toContain('(ok)')
    expect(await terminalText(page)).not.toContain('\\x1b[')
    await page.screenshot({ path: testInfo.outputPath('after-switch.png'), fullPage: true })
  })
}

test('reported session: current terminal connects and renders', async ({ page }, testInfo) => {
  const id = process.env.OMNI_ANSI_SESSION
  test.skip(!id, 'Set OMNI_ANSI_SESSION to check a currently running session')
  const response = await page.request.get('/api/cc/sessions?include_recoverable=false')
  expect(response.ok()).toBe(true)
  const listing = await response.json()
  test.skip(!listing.items.some((item: { id: string; alive: boolean }) => item.id === id && item.alive),
    'The reported session is no longer running; avoid resuming it during a rendering test')
  await page.goto(`/?${new URLSearchParams({ open_type: 'cc_session', open_id: id!, open_title: 'https://workshop.colorc.' })}`)
  const terminal = page.locator(`[data-cc-session-id="${id}"]`)
  await expect(terminal.locator('.xterm')).toBeVisible()
  await expect(terminal).toHaveAttribute('data-cc-session-connected', 'true')
  await expect(terminal.getByTestId('cc-terminal-restoring')).toHaveCount(0)
  const state = await page.evaluate((sessionId) => {
    const inspect = (window as Window & {
      __OMNI_CLIENT_INSPECT__?: { info: () => { terminals: Array<{ sessionId: string; cols: number; rows: number; visible: boolean }> } }
    }).__OMNI_CLIENT_INSPECT__
    const terminal = inspect?.info().terminals.find((terminal) => terminal.sessionId === sessionId)
    return terminal ? { cols: terminal.cols, rows: terminal.rows, visible: terminal.visible } : null
  }, id)
  expect(state?.cols).toBeGreaterThan(1)
  expect(state?.rows).toBeGreaterThan(0)
  expect(state?.visible).toBe(true)
  await expect(terminal.locator('.xterm-screen')).toBeVisible()
  // Long-lived TUIs can have blank leading scrollback; inspect.lines caps that history,
  // so verify the current viewport visually instead of mistaking the capped prefix for it.
  await page.screenshot({ path: testInfo.outputPath('reported-session.png'), fullPage: true })
})
