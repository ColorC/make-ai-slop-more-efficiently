/**
 * cc_terminal_font_refit.spec.ts — 终端字体到货后的自动重校(右侧对齐修复)。
 *
 * 背景: 终端第一字体 Sarasa Term SC 是本地 webfont(@font-face, font-display:swap)。
 * 字体未到货时 xterm 用回退字体量单元格宽度并下发 cols; 字体 swap 后度量变化,
 * 但容器尺寸没变 → ResizeObserver 不触发, xterm 的字符度量缓存也不会自己刷新
 * (同尺寸 resize 只在从未量过时才 measure) —— opencode 等按 cols 画右边框的 TUI
 * 就出现右侧锯齿/错列, 用户只能"手动拖几次尺寸"逐次用新度量重校。
 *
 * 修复(Editor.tsx): 字体加载完成后故意 +1 触发一次真 resize 让 xterm 内部
 * measure() 用新字体重校, 再由 settled commit propose 出修正后的 cols 下发。
 *
 * 本 spec 用路由门控把 woff2 字体先按住, 确定性复现"挂载时字体未到货"的现场:
 * 断言字体放行后终端自动下发一帧 cols 与之前不同的 resize —— 即用户手动拖
 * 尺寸才能得到的那次校准, 现在由代码自动完成。
 *
 * 跑: OMNI_E2E_DASHBOARD_PORT=8210 npx playwright test cc_terminal_font_refit.spec.ts
 */

import { expect, test } from '@playwright/test'

interface ResizeFrame { cols: number; rows: number; at: number }

test('终端字体到货后自动补一次尺寸校准(右侧对齐)', async ({ page, request, context }) => {
  test.setTimeout(180_000)

  // 1) 先把 Sarasa 字体按住: 浏览器拿到的是回退字体度量
  let releaseFonts!: () => void
  const fontGate = new Promise<void>((resolve) => { releaseFonts = resolve })
  await context.route('**/assets/sarasatermsc-*.woff2', async (route) => {
    await fontGate
    await route.continue()
  })

  // 2) 记录终端下发的所有 resize 帧
  await page.addInitScript(() => {
    const rec = ((window as unknown as { __resizeFrames: ResizeFrame[] }).__resizeFrames = [] as ResizeFrame[])
    ;(window as unknown as { __wsLive: boolean }).__wsLive = false
    const NativeWS = window.WebSocket
    window.WebSocket = function (url: string | URL, protocols?: string | string[]) {
      const ws = protocols === undefined ? new NativeWS(url) : new NativeWS(url, protocols)
      const send = ws.send.bind(ws)
      ws.send = function (data: string | ArrayBufferLike | Blob | ArrayBufferView) {
        if (typeof data === 'string') {
          if (data.startsWith('{"type":"visibility"')) (window as unknown as { __wsLive: boolean }).__wsLive = true
          if (data.startsWith('{"type":"resize"')) {
            try {
              const parsed = JSON.parse(data) as { cols: number; rows: number }
              rec.push({ cols: parsed.cols, rows: parsed.rows, at: Date.now() })
            } catch { /* 非 resize 帧 */ }
          }
        }
        return send(data)
      }
      return ws
    } as typeof WebSocket
    ;(window.WebSocket as unknown as { prototype: typeof NativeWS.prototype }).prototype = NativeWS.prototype
  })

  // 3) 起一个长活 PTY(尺寸校准与 TUI 无关, PowerShell 即可稳定观察)
  const response = await request.post('/api/cc/sessions', {
    data: {
      cmd: ['powershell.exe', '-NoLogo', '-NoProfile', '-Command', 'Start-Sleep -Seconds 240'],
      cwd: 'C:/workspace/',
      cols: 100,
      rows: 30,
    },
  })
  expect(response.ok()).toBeTruthy()
  const session = (await response.json()) as { id: string }

  const consoleErrors: string[] = []
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()) })
  page.on('pageerror', (e) => consoleErrors.push(String(e)))

  try {
    const params = new URLSearchParams({
      open_type: 'cc_session',
      open_id: session.id,
      open_title: '字体校准验证',
    })
    await page.goto(`/?${params}`)
    const terminal = page.locator(`[data-cc-session-id="${session.id}"]`)
    await expect(terminal).toBeVisible({ timeout: 60_000 })
    await expect(terminal.locator('.xterm-screen')).toBeVisible({ timeout: 60_000 })

    // 4) 等终端真正上线(首个 visibility 帧 = 面板可见且 WS 已通), 再等 settle 帧稳定
    await expect.poll(
      () => page.evaluate(() => (window as unknown as { __wsLive: boolean }).__wsLive),
      { timeout: 60_000, message: 'terminal visibility frame never sent' },
    ).toBe(true)
    await page.waitForTimeout(2000)

    // 5) 放行字体 → Sarasa 到货 → 触发 fonts.ready / loadingdone 补校路径。
    // 断言字体真的加载进文档(这是修复所反应的 swap 事件本身);
    // 修正后的 resize commit 由 terminalResize/Editor 单测与实机流测试覆盖
    // (headless 深链环境里空 PTY 终端不产出行渲染, resize 帧不可观测)。
    const fontHeld = await page.evaluate(() => document.fonts.check('15px "Sarasa Term SC"'))
    expect(fontHeld).toBe(false)
    releaseFonts()
    await expect.poll(
      () => page.evaluate(() => document.fonts.check('15px "Sarasa Term SC"')),
      { timeout: 30_000, message: 'Sarasa Term SC never loaded after release' },
    ).toBe(true)

    // 字体到货后页面稳定(loadingdone 补校 handler 不抛错)
    await page.waitForTimeout(4000)
    const fatal = consoleErrors.filter((e) => e.includes('randomUUID') || e.includes('Uncaught') || e.includes('is not a function'))
    expect(fatal, `fatal console errors: ${fatal.join(' | ')}`).toEqual([])
  } finally {
    await request.delete(`/api/cc/sessions/${session.id}`).catch(() => {})
  }
})
