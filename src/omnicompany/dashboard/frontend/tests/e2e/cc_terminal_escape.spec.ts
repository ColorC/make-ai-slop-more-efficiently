import { expect, test, type Page } from '@playwright/test'
import { fileURLToPath } from 'node:url'

async function terminalText(page: Page, sessionId: string): Promise<string> {
  return page.evaluate((id) => {
    const inspect = (window as Window & {
      __OMNI_CLIENT_INSPECT__?: { info: () => { terminals: Array<{ sessionId: string; lines: string[] }> } }
    }).__OMNI_CLIENT_INSPECT__
    return inspect?.info().terminals.find((terminal) => terminal.sessionId === id)?.lines.join('\n') || ''
  }, sessionId)
}

function terminalDeepLink(sessionId: string): string {
  const params = new URLSearchParams({
    open_type: 'cc_session',
    open_id: sessionId,
    open_title: '终端 Escape 回归',
  })
  return `/?${params}`
}

type ClientFrame = { type?: string; data?: string }
const KEY_READER = [
  'import os, ctypes',
  'kernel = ctypes.windll.kernel32',
  'assert kernel.SetConsoleMode(kernel.GetStdHandle(-10), 0x0200)',
  "print('ESC_READY', flush=True)",
  'while True:',
  '    data = os.read(0, 1)',
  "    print('KEY:' + str(data[0]), flush=True)",
].join('\n')

const MODES = [
  { name: 'desktop browser', context: {}, init: undefined, syntheticDocumentEscape: false },
  {
    name: 'touch desktop host',
    context: { hasTouch: true, viewport: { width: 1280, height: 800 } },
    init: () => {
      Object.defineProperty(navigator, 'maxTouchPoints', { configurable: true, value: 5 })
      window.__OMNICOMPANY_HOST__ = { runtime: 'desktop' }
    },
    syntheticDocumentEscape: false,
  },
  {
    name: 'tablet touch context',
    context: { hasTouch: true, viewport: { width: 834, height: 1194 } },
    init: () => Object.defineProperty(navigator, 'maxTouchPoints', { configurable: true, value: 5 }),
    syntheticDocumentEscape: true,
  },
] as const

for (const mode of MODES) test.describe(mode.name, () => {
  test.use(mode.context)
  test('聚焦 xterm 的硬件 Escape 只发送一个 PTY input', async ({ page, request }, testInfo) => {
  if (mode.init) await page.addInitScript(mode.init)
  const response = await request.post('/api/cc/sessions', {
    data: {
      cmd: ['python.exe', '-u', '-c', KEY_READER],
      cwd: fileURLToPath(new URL('../../../', import.meta.url)),
      cols: 100,
      rows: 30,
    },
  })
  expect(response.ok()).toBeTruthy()
  const session = await response.json() as { id: string }
  const inputFrames: ClientFrame[] = []

  await page.routeWebSocket(`**/api/cc/sessions/${session.id}/ws**`, async (socket) => {
    const server = await socket.connectToServer()
    socket.onMessage((message) => {
      if (typeof message !== 'string') return
      try {
        const frame = JSON.parse(message) as ClientFrame
        if (frame.type === 'input') inputFrames.push(frame)
      } catch {
        // Binary/output frames are irrelevant to this routing assertion.
      }
      server.send(message)
    })
    server.onMessage((message) => {
      socket.send(message)
    })
  })

  try {
    await page.goto(terminalDeepLink(session.id))
    const terminal = page.locator(`[data-cc-session-id="${session.id}"]`)
    await expect(terminal.locator('[data-cc-term] .xterm')).toBeVisible({ timeout: 15_000 })
    await expect(terminal).toHaveAttribute('data-cc-session-connected', 'true', { timeout: 15_000 })
    await expect(terminal.getByTestId('cc-terminal-restoring')).toHaveCount(0, { timeout: 15_000 })
    await expect.poll(() => terminalText(page, session.id), { timeout: 10_000 }).toContain('ESC_READY')

    const xtermInput = terminal.locator('.xterm-helper-textarea')
    await xtermInput.focus()
    await expect(xtermInput).toBeFocused()
    await page.evaluate(() => {
      const seen: string[] = []
      ;(window as Window & { __escapeBubbles?: string[] }).__escapeBubbles = seen
      for (const type of ['keydown', 'keyup', 'keypress']) {
        window.addEventListener(type, (event) => seen.push(`window:${type}:${(event as KeyboardEvent).key}`))
        document.addEventListener(type, (event) => seen.push(`document:${type}:${(event as KeyboardEvent).key}`))
      }
    })
    const beforeEscape = inputFrames.length
    await page.keyboard.press('Escape')
    await expect.poll(() => inputFrames.length, { timeout: 5_000 }).toBeGreaterThan(beforeEscape)
    const rawEscapeCount = () => inputFrames.slice(beforeEscape).filter((frame) => frame.data === '\x1b').length
    expect(rawEscapeCount()).toBe(1)
    await expect.poll(() => terminalText(page, session.id), { timeout: 10_000 }).toContain('KEY:27')
    expect(await page.evaluate(() => (window as Window & { __escapeBubbles?: string[] }).__escapeBubbles ?? [])).toEqual([])

    // keyup/keypress after the hardware keydown must not create another PTY frame.
    await xtermInput.dispatchEvent('keyup', { key: 'Escape', code: 'Escape' })
    await xtermInput.dispatchEvent('keypress', { key: 'Escape', code: 'Escape' })
    if (mode.syntheticDocumentEscape) {
      await page.evaluate(() => document.dispatchEvent(new KeyboardEvent('keydown', {
        key: 'Escape', code: 'Escape', bubbles: true, cancelable: true,
      })))
      await expect.poll(rawEscapeCount, { timeout: 5_000 }).toBe(2)
    }
    await page.waitForTimeout(250)
    expect(rawEscapeCount()).toBe(mode.syntheticDocumentEscape ? 2 : 1)
    await page.screenshot({ path: testInfo.outputPath('terminal-escape.png'), fullPage: true })

    // Prove the same key outside the terminal does not reach this session's WS input.
    await page.evaluate(() => {
      const input = document.createElement('input')
      input.setAttribute('data-testid', 'escape-outside-input')
      input.style.position = 'fixed'
      input.style.left = '4px'
      input.style.top = '4px'
      document.body.appendChild(input)
      input.focus()
    })
    await page.keyboard.press('Escape')
    await page.waitForTimeout(250)
    expect(rawEscapeCount()).toBe(mode.syntheticDocumentEscape ? 2 : 1)
  } catch (error) {
    const output = await request.get(`/api/cc/sessions/${session.id}/output`, { timeout: 5_000 })
    console.log('Test PTY output:', await output.text())
    throw error
  } finally {
    // Give teardown its own budget even when an assertion consumed the test timeout.
    test.setTimeout(testInfo.timeout + 15_000)
    const deleted = await request.delete(`/api/cc/sessions/${session.id}`, { timeout: 10_000 })
    expect([200, 404]).toContain(deleted.status())
    const remaining = await request.get('/api/cc/sessions?include_recoverable=false', { timeout: 10_000 })
    expect(remaining.ok()).toBe(true)
    const listing = await remaining.json()
    const sessions = listing.items
    expect(sessions.some((item: { id: string; alive: boolean }) => item.id === session.id && item.alive)).toBe(false)
  }
  })
})
