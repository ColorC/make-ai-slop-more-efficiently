import { expect, test } from '@playwright/test'
import { SHOTS, setupErrorLogging } from './helpers'

const CJK_MARKER = '中文终端验证'

function terminalDeepLink(sessionId: string): string {
  const params = new URLSearchParams({
    open_type: 'cc_session',
    open_id: sessionId,
    open_title: '终端视觉验证',
  })
  return `/?${params}`
}

async function copyTerminalBuffer(page: import('@playwright/test').Page, sessionId: string): Promise<string> {
  const root = page.locator(`[data-cc-session-id="${sessionId}"]`)
  const input = root.locator('.xterm-helper-textarea')
  await input.click()
  await page.keyboard.press('Control+a')
  await page.keyboard.press('Control+c')
  await expect.poll(
    () => page.evaluate(() => navigator.clipboard.readText()),
    { timeout: 5_000 },
  ).toContain(CJK_MARKER)
  const copied = await page.evaluate(() => navigator.clipboard.readText())
  // Ctrl+A intentionally paints xterm's selection colour over the whole viewport.
  // Clear that selection before visual assertions/screenshots without sending PTY input.
  await root.locator('.xterm-screen').click({ position: { x: 4, y: 4 } })
  return copied
}

test('真实 PTY 保留中文、ANSI 主题、伴随侧栏和关闭后重开布局', async ({ page, request, context }) => {
  setupErrorLogging(page)
  const unexpectedHttpErrors: string[] = []
  page.on('response', (response) => {
    if (
      response.status() < 400
      || response.url().endsWith('/favicon.ico')
      // Existing project-board bootstrap gap, unrelated to the cc_session
      // surface under test. Keep this test focused on terminal/sidecar traffic.
      || response.url().endsWith('/api/project-views')
    ) return
    unexpectedHttpErrors.push(`${response.status()} ${response.url()}`)
  })
  await context.grantPermissions(['clipboard-read', 'clipboard-write'])

  const esc = '$esc=[char]27'
  const output = `Write-Output ($esc+'[31mANSI_RED 红色'+$esc+'[0m | '+$esc+'[32mANSI_GREEN 绿色'+$esc+'[0m | ${CJK_MARKER}')`
  const keepAlive = 'Start-Sleep -Seconds 180'
  const response = await request.post('/api/cc/sessions', {
    data: {
      cmd: [
        'powershell.exe',
        '-NoLogo',
        '-NoProfile',
        '-Command',
        `[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); ${esc}; ${output}; ${keepAlive}`,
      ],
      cwd: 'C:/workspace/',
      cols: 100,
      rows: 30,
    },
  })
  expect(response.ok()).toBeTruthy()
  const session = await response.json() as { id: string }

  try {
    await page.goto(terminalDeepLink(session.id))
    const terminal = page.locator(`[data-cc-session-id="${session.id}"]`)
    await expect(terminal).toBeVisible({ timeout: 15_000 })
    await expect(terminal.locator('[data-cc-term] .xterm')).toBeVisible({ timeout: 15_000 })
    await expect(terminal).toHaveAttribute('data-cc-session-connected', 'true', { timeout: 15_000 })

    const copiedBeforeClose = await copyTerminalBuffer(page, session.id)
    expect(copiedBeforeClose).toContain('ANSI_RED 红色')
    expect(copiedBeforeClose).toContain('ANSI_GREEN 绿色')

    const visual = await terminal.locator('[data-cc-term]').evaluate((element) => {
      const root = getComputedStyle(document.documentElement)
      const terminalStyle = getComputedStyle(element)
      return {
        background: terminalStyle.backgroundColor,
        expectedBackground: root.getPropertyValue('--fp-bp-solid').trim(),
        ansiRed: root.getPropertyValue('--fp-err').trim(),
        ansiGreen: root.getPropertyValue('--fp-ok').trim(),
      }
    })
    expect(visual.background).not.toBe('rgba(0, 0, 0, 0)')
    expect(visual.expectedBackground).not.toBe('')
    expect(visual.ansiRed).not.toBe('')
    expect(visual.ansiGreen).not.toBe('')
    expect(visual.ansiRed).not.toBe(visual.ansiGreen)
    // xterm 6 + WebGL renders glyphs into canvas; its hidden IME textarea
    // intentionally inherits the surrounding UI font and is not a font probe.
    await expect(terminal.locator('.xterm-screen canvas').first()).toBeVisible()

    const sidecarLayout = page.getByTestId('tab-sidecar-layout')
    await expect(sidecarLayout).toHaveAttribute('data-sidecar-collapsed', '0')
    await expect(page.getByTestId('tab-sidecar')).toBeVisible()
    await expect(page.getByTestId('session-companion')).toHaveAttribute('data-page', 'multiagent')
    const layoutBefore = await sidecarLayout.evaluate((element) => {
      const main = element.querySelector<HTMLElement>('.tab-sidecar-main')
      const aside = element.querySelector<HTMLElement>('.tab-sidecar-aside')
      return {
        mainWidth: main?.getBoundingClientRect().width || 0,
        sidecarWidth: aside?.getBoundingClientRect().width || 0,
      }
    })
    expect(layoutBefore.mainWidth).toBeGreaterThan(400)
    expect(layoutBefore.sidecarWidth).toBeGreaterThanOrEqual(280)

    await page.screenshot({ path: `${SHOTS}/cc_terminal_visual_before_close.png`, fullPage: true })

    const activeTab = page.locator('.dv-active-tab').filter({ has: page.locator('.omni-cc-session-tab') })
    await activeTab.locator('.dv-default-tab-action').click()
    await expect(terminal).toHaveCount(0)

    await page.goto(terminalDeepLink(session.id))
    const reopened = page.locator(`[data-cc-session-id="${session.id}"]`)
    await expect(reopened.locator('[data-cc-term] .xterm')).toBeVisible({ timeout: 15_000 })
    await expect(reopened).toHaveAttribute('data-cc-session-connected', 'true', { timeout: 15_000 })
    expect(await copyTerminalBuffer(page, session.id)).toContain(CJK_MARKER)
    await expect(page.getByTestId('tab-sidecar-layout')).toHaveAttribute('data-sidecar-collapsed', '0')
    await expect(page.getByTestId('session-companion')).toHaveAttribute('data-page', 'multiagent')

    await page.screenshot({ path: `${SHOTS}/cc_terminal_visual_reopened.png`, fullPage: true })
    expect(unexpectedHttpErrors).toEqual([])
  } finally {
    await request.delete(`/api/cc/sessions/${session.id}`).catch(() => undefined)
  }
})
