import { expect, test } from '@playwright/test'
import { fileURLToPath } from 'node:url'
import { execFileSync } from 'node:child_process'

const READER = [
  'import os, ctypes',
  'kernel = ctypes.windll.kernel32',
  'assert kernel.SetConsoleMode(kernel.GetStdHandle(-10), 0x0200)',
  "print('\\x1b[?1003;1006h', end='', flush=True)",
  "print('\\r\\x1b[2KWHEEL_READY', flush=True)",
  'while True:',
  '    data = os.read(0, 1)',
  "    print('KEY:' + str(data[0]), flush=True)",
].join('\n')

for (const legacy of [false, true]) test(`wheel after ${legacy ? 'legacy' : 'mode-preserving'} screen repaint`, async ({ page, request }, testInfo) => {
  const repo = fileURLToPath(new URL('../../../../../../',import.meta.url))
  const snapshot = execFileSync(`${repo}/venv/Scripts/python.exe`, ['-c',
    "from omnicompany.dashboard.ccdaemon.pty_screen import ScreenModel; m=ScreenModel(90,30); m.feed('\\x1b[?1003;1006hWHEEL_READY'); print(m.render_frame(),end='')",
  ], {encoding:'utf8',env:{...process.env,PYTHONPATH:`${repo}/src`},windowsHide:true})
  const created = await request.post('/api/cc/sessions', { data: {
    cmd: [`${repo}/venv/Scripts/python.exe`,'-u','-c',READER],
    cwd: fileURLToPath(new URL('../../../', import.meta.url)),
    cols: 90, rows: 30,
  } })
  expect(created.ok(), await created.text()).toBeTruthy()
  const session = await created.json()
  await page.route(url => url.pathname === '/api/cc/sessions', async route => {
    await route.fulfill({json:{items:[{...session,provider:'codex'}]}})
  })
  const inputs: string[] = []
  let snapshotModeCount = 0
  await page.routeWebSocket(`**/api/cc/sessions/${session.id}/ws**`, async socket => {
    const server = await socket.connectToServer()
    socket.onMessage(message => {
      const frame = JSON.parse(String(message))
      if (frame.type === 'input') inputs.push(frame.data)
      server.send(message)
    })
    server.onMessage(message => {
      const frame = JSON.parse(String(message))
      if (frame.type === 'snapshot_begin') frame.meta = {...frame.meta,provider:'codex',screen_model:true}
      if (frame.type === 'snapshot_chunk') {
        frame.chunks = [snapshot]
        for (const chunk of frame.chunks) snapshotModeCount += (chunk.match(/\x1b\[\?100[036]h/g) || []).length
        // Reproduce a host already running the previous renderer, without
        // changing any user session or the shared server's implementation.
        if (legacy) frame.chunks = frame.chunks.map((chunk: string) => chunk.replace(/\x1b\[\?(?:1000|1002|1003|1006)h/g,''))
      }
      socket.send(JSON.stringify(frame))
    })
  })
  try {
    await expect.poll(async () => (await (await request.get(`/api/cc/sessions/${session.id}/output`)).json()).text, {timeout:15000}).toContain('WHEEL_READY')
    await page.goto(`/?open_type=cc_session&open_id=${session.id}`)
    const terminal = page.locator(`[data-cc-session-id="${session.id}"]`)
    await expect(terminal.locator('.xterm')).toBeVisible({timeout:30000})
    await expect(terminal).toHaveAttribute('data-cc-session-connected','true',{timeout:15000})
    await expect.poll(() => snapshotModeCount,{timeout:15000}).toBeGreaterThan(0)
    await expect.poll(() => page.evaluate((id) => {
      return (window as Window & {__OMNI_CLIENT_INSPECT__?: {info:()=>{terminals:Array<{sessionId:string;lines:string[]}>}}})
        .__OMNI_CLIENT_INSPECT__?.info().terminals.find(t=>t.sessionId===id)?.lines.join('\n') || ''
    },session.id),{timeout:15000}).toContain('WHEEL_READY')
    await expect(terminal.getByTestId('cc-terminal-restoring')).toHaveCount(0,{timeout:20000})
    const xterm = terminal.locator('.xterm-screen')
    await xterm.hover()
    inputs.length = 0
    await page.mouse.wheel(0,-120)
    await expect.poll(() => inputs.length, {timeout:5000}).toBeGreaterThan(0)
    expect(inputs.some(input=>legacy ? input === '\x1b[5~' : input.startsWith('\x1b[<64;'))).toBe(true)
    inputs.length = 0
    await page.mouse.wheel(0,120)
    await expect.poll(() => inputs.length, {timeout:5000}).toBeGreaterThan(0)
    expect(inputs.some(input=>legacy ? input === '\x1b[6~' : input.startsWith('\x1b[<65;'))).toBe(true)
    // The native PTY receives the report, not just a browser event.
    await expect.poll(async () => (await (await request.get(`/api/cc/sessions/${session.id}/output`)).json()).text,{timeout:5000}).toContain('KEY:27')
    await page.screenshot({path:testInfo.outputPath('wheel-recovery.png')})
  } finally {
    test.setTimeout(testInfo.timeout + 15000)
    await page.unrouteAll({behavior:'ignoreErrors'})
    const deleted = await request.delete(`/api/cc/sessions/${session.id}`,{timeout:10000})
    expect([200,404]).toContain(deleted.status())
  }
})
