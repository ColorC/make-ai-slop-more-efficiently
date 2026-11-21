import { expect, test, type Page, type WebSocketRoute } from '@playwright/test'

const SESSION = 'terminal-geometry-scroll-fixture'
const URL = 'https://example.com/reports/remote-terminal-history?view=complete&revision=1234567890'

async function state(page: Page) {
  return page.evaluate((id) => (window as unknown as {
    __OMNI_CLIENT_INSPECT__: { info(): { terminals: Array<{ sessionId: string; cols: number; rows: number; lines: string[]; viewportY: number; baseY: number; viewportLines: string[] }> } }
  }).__OMNI_CLIENT_INSPECT__?.info().terminals.find(item => item.sessionId === id), SESSION)
}

async function fixture(page: Page) {
  await page.route(/\/api\/cc\/sessions(?:\?.*)?$/, route => route.fulfill({ json: {
    items: [{ id: SESSION, cmd: ['shell'], cwd: 'C:/workspace/omnicompany',
      cols: 100, rows: 30, alive: true, provider: 'shell', display_title: 'PTY geometry regression' }],
    recoverable: [],
  } }))
  let socket!: WebSocketRoute
  const inputs: string[] = []
  await page.routeWebSocket(`**/api/cc/sessions/${SESSION}/ws**`, route => {
    socket = route
    route.onMessage(message => {
      const frame = JSON.parse(String(message))
      if (frame.type === 'input') inputs.push(frame.data)
      if (frame.type === 'attach') route.send(JSON.stringify({ type: 'snapshot', chunks: ['READY\r\n'] }))
    })
  })
  await page.goto(`/?open_type=cc_session&open_id=${SESSION}`)
  const root = page.locator(`[data-cc-session-id="${SESSION}"]`)
  await expect(root.locator('.xterm')).toBeVisible()
  await expect(root.getByTestId('cc-terminal-restoring')).toHaveCount(0)
  await page.evaluate(() => document.fonts.ready)
  // Allow the initial settled fit to finish before injecting an independent server event.
  await page.waitForTimeout(400)
  return { root, inputs, send: (frame: unknown) => socket.send(JSON.stringify(frame)) }
}

test('font loading after a settled fit keeps the PTY grid unchanged', async ({ page }) => {
  await fixture(page)
  const before = await state(page)
  await page.evaluate(() => document.fonts.dispatchEvent(new Event('loadingdone')))
  await page.waitForTimeout(300)
  const after = await state(page)
  expect({ cols: after?.cols, rows: after?.rows }).toEqual({ cols: before?.cols, rows: before?.rows })
})

test('snapshot uses its recorded grid before parsing cursor-positioned rows', async ({ page }, testInfo) => {
  const { root, send } = await fixture(page)
  send({ type: 'snapshot_begin', meta: { cols: 37, rows: 12, screen_model: true, screen_frame: true, provider: 'shell' } })
  send({ type: 'snapshot_chunk', chunks: ['R'.repeat(37) + '\r\nSECOND_ROW\x1b[12;1HBOTTOM_ROW'] })
  send({ type: 'snapshot_end' })
  await expect.poll(async () => (await state(page))?.lines.join('\n')).toContain('BOTTOM_ROW')
  expect((await state(page))?.cols).toBe(37)
  expect((await state(page))?.rows).toBe(12)
  await expect(root.getByTestId('cc-terminal-restoring')).toHaveCount(0)
  await page.screenshot({ path: testInfo.outputPath('snapshot-grid.png') })
})

test('normal history scrolls and a server grid wider than the pane remains reachable', async ({ page }, testInfo) => {
  const { root, send, inputs } = await fixture(page)
  send({ type: 'geometry', cols: 220, rows: 30 })
  send({ type: 'output', seq: 1, data: Array.from({ length: 150 }, (_, i) => `HISTORY_${i}`).join('\r\n') + '\r\n' + ' '.repeat(130) + URL + '\r\n' })
  await expect.poll(async () => (await state(page))?.lines.join('\n')).toContain(URL)
  await root.locator('.xterm-screen').hover({ position: { x: 30, y: 60 } })
  await page.mouse.wheel(0, -650)
  await expect(root.getByRole('button', { name: /最新/ })).toBeVisible()
  await expect.poll(async () => {
    const terminal = await state(page)
    return Boolean(terminal && terminal.viewportY < terminal.baseY && terminal.viewportLines.some(line => line.startsWith('HISTORY_')))
  }).toBe(true)
  expect(inputs).toEqual([])
  await page.screenshot({ path: testInfo.outputPath('history-scroll.png') })
  const overflow = await root.locator('[data-cc-term]').evaluate(element => {
    const before = element.scrollLeft
    element.scrollLeft = element.scrollWidth
    return { moved: element.scrollLeft > before, overflowX: getComputedStyle(element).overflowX }
  })
  expect(overflow.moved).toBe(true)
  expect(overflow.overflowX).toBe('auto')
  // Font events and the scrollbar's layout change must not take over the PTY
  // or temporarily parse output at this narrow viewer's own fitted grid.
  await page.evaluate(() => document.fonts.dispatchEvent(new Event('loadingdone')))
  await page.waitForTimeout(300)
  expect((await state(page))?.cols).toBe(220)
})

test('clicking a visible prefix in narrowed history opens the entire URL', async ({ page }, testInfo) => {
  const { root, send } = await fixture(page)
  send({ type: 'geometry', cols: 137, rows: 20 })
  send({ type: 'output', seq: 1, data: '\x1b[2J\x1b[H  ' + URL + '\r\n' + 'HISTORY\r\n'.repeat(35) })
  await expect.poll(async () => (await state(page))?.lines.join('\n')).toContain(URL)
  send({ type: 'geometry', cols: 55, rows: 20 })
  await expect.poll(async () => (await state(page))?.cols).toBe(55)
  await root.locator('.xterm-screen').hover({ position: { x: 20, y: 60 } })
  for (let i = 0; i < 12; i++) await page.mouse.wheel(0, -1000)
  await expect(root.getByTestId('cc-terminal-latest')).toBeVisible()
  await testInfo.attach('scrolled-state', { body: JSON.stringify(await state(page), null, 2), contentType: 'application/json' })
  await expect.poll(async () => (await state(page))?.viewportY).toBe(0)
  const opened: string[] = []
  await page.exposeFunction('captureTerminalUrl', (url: string) => opened.push(url))
  await page.evaluate(() => {
    window.open = ((url: string) => {
      void (window as unknown as { captureTerminalUrl(url: string): Promise<void> }).captureTerminalUrl(String(url))
      return null
    }) as typeof window.open
  })
  const screen = root.locator('.xterm-screen')
  const rect = await screen.boundingBox()
  if (!rect) throw new Error('terminal screen has no layout')
  await screen.click({ position: { x: (rect.width / 55) * 6, y: rect.height / 20 / 2 } })
  await expect.poll(async () => opened[0] || await page.getByTestId('web-page-address').inputValue().catch(() => '')).toBe(URL)
})
