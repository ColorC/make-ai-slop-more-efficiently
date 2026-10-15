/**
 * dsh_web_session.spec.ts — DeepSeek Harness Web UI 会话的真实浏览器 e2e。
 *
 * 用真实浏览器(Chromium)通过**非 loopback 局域网地址**打开统一通道代理页,
 * 复现并验证三个只在真浏览器里出现的问题:
 *   1. crypto.randomUUID 是 secure-context-only API, LAN http 下 undefined →
 *      注入 shim 的 getRandomValues 兜底必须让它存在且 UI 能连上;
 *   2. 客户端把 WS 地址 protocol 换成 ws: 后 origin 不再等于 location.origin →
 *      shim 必须按 host 重写, WS 必须走 /api/cc/web/<sid>/api/events.* 前缀;
 *   3. 默认工作区: 会话 cwd 已被引导注册为 workspace, 侧栏/作曲器里可见并被选中。
 * 最后通过真实 UI(textarea 作曲器 + Enter)发一条真模型任务, 断言回答文本出现在 DOM。
 *
 * 前置: OMNI_E2E_DASHBOARD_PORT=8210(复用已起的 dashboard + ccdaemon);
 *       网关 Basic 凭据在 ~/codeweb/omni-access-credentials.txt(缺则 skip);
 *       机器需要可达的局域网 IPv4(缺则 skip, 用 127.0.0.1 无法复现 secure-context 缺口)。
 */

import { test, expect } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { networkInterfaces } from 'node:os'
import { join } from 'node:path'

const DASH_PORT = Number(process.env.OMNI_E2E_DASHBOARD_PORT || 8210)

function lanIpv4(): string | null {
  for (const addrs of Object.values(networkInterfaces())) {
    for (const addr of addrs || []) {
      if (addr.family === 'IPv4' && !addr.internal && !addr.address.startsWith('169.254')) {
        return addr.address
      }
    }
  }
  return null
}

function gatewayCreds(): { username: string; password: string } | null {
  try {
    const raw = readFileSync(join(homedir(), 'codeweb', 'omni-access-credentials.txt'), 'utf8')
    const cred: Record<string, string> = {}
    for (const line of raw.split(/\r?\n/).map((l) => l.trim()).filter(Boolean)) {
      const m = line.match(/^(username|password)\s*[:=]\s*(.+)$/i)
      if (m) cred[m[1].toLowerCase()] = m[2].trim()
    }
    return cred.username && cred.password
      ? { username: cred.username, password: cred.password }
      : null
  } catch {
    return null
  }
}

async function createWebSession(): Promise<string> {
  const res = await fetch(`http://127.0.0.1:${DASH_PORT}/api/cc/agent-workers`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ provider: 'dsh_web', origin: 'human', visibility: 'visible' }),
  })
  if (!res.ok) throw new Error(`agent-workers: ${res.status}`)
  const meta = await res.json() as { id: string }
  for (let i = 0; i < 30; i++) {
    await new Promise((r) => setTimeout(r, 1000))
    const ts = await (await fetch(`http://127.0.0.1:${DASH_PORT}/api/cc/tab-states`)).json() as {
      web: Array<{ id: string; web_url?: string | null; web_phase?: string }>
    }
    const row = (ts.web || []).find((x) => x.id === meta.id)
    if (row?.web_url) return meta.id
  }
  throw new Error('web session never became ready')
}

async function deleteSession(sid: string): Promise<void> {
  await fetch(`http://127.0.0.1:${DASH_PORT}/api/cc/sessions/${sid}`, { method: 'DELETE' }).catch(() => {})
}

test('dsh web UI boots over LAN http, rewrites WS, auto-selects default workspace, and runs a real model turn', async ({ page }) => {
  test.setTimeout(300_000)
  const ip = lanIpv4()
  const creds = gatewayCreds()
  test.skip(!ip || !creds, `需要局域网 IPv4 + ~/codeweb/omni-access-credentials.txt (ip=${ip ?? 'none'}, creds=${creds ? 'ok' : 'missing'})`)

  const sid = await createWebSession()
  try {
    await page.context().setHTTPCredentials(creds)
    await page.context().setExtraHTTPHeaders({})
    // 记录到达原生 WebSocket 的 URL —— shim 重写成功后必须带会话前缀
    await page.addInitScript(() => {
      const log = ((window as unknown as { __wsProbe: string[] }).__wsProbe = [] as string[])
      const NativeWS = window.WebSocket
      window.WebSocket = function (url: string | URL, protocols?: string | string[]) {
        log.push(String(url instanceof URL ? url.href : url))
        return protocols === undefined ? new NativeWS(url) : new NativeWS(url, protocols)
      } as typeof WebSocket
      ;(window.WebSocket as unknown as { prototype: typeof NativeWS.prototype }).prototype = NativeWS.prototype
    })
    const consoleErrors: string[] = []
    page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()) })
    page.on('pageerror', (e) => consoleErrors.push(String(e)))

    const url = `http://${ip}:${DASH_PORT}/api/cc/web/${sid}/`
    await page.goto(url, { timeout: 90_000 })

    // 1) crypto.randomUUID 在非安全上下文必须可用(否则所有 RPC 直接抛)
    const hasUuid = await page.evaluate(() => typeof crypto.randomUUID === 'function')
    expect(hasUuid, 'crypto.randomUUID polyfill must be active on LAN http').toBe(true)
    const uuidValue = await page.evaluate(() => crypto.randomUUID())
    expect(uuidValue).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i)

    // 2) UI 真正启动: 标题 + 工作区侧栏(默认工作区 WindowsWorkspace 必须可见)
    await expect(page).toHaveTitle(/DeepSeek Harness/, { timeout: 60_000 })
    await expect(page.getByText('WindowsWorkspace').first()).toBeVisible({ timeout: 60_000 })

    // 3) WS 必须走统一通道路径前缀(等重连周期稳定后取前几条)
    await page.waitForTimeout(5000)
    const wsUrls = await page.evaluate(() => (window as unknown as { __wsProbe: string[] }).__wsProbe)
    expect(wsUrls.length, `ws probe urls: ${JSON.stringify(wsUrls)}`).toBeGreaterThan(0)
    for (const wsUrl of wsUrls.slice(0, 2)) {
      expect(wsUrl, 'websocket must go through the unified channel prefix').toContain(`/api/cc/web/${sid}/api/events.`)
    }

    // 4) 无 randomUUID / 403 类致命错误
    const fatal = consoleErrors.filter((e) => e.includes('randomUUID') || e.includes('403'))
    expect(fatal, `fatal console errors: ${fatal.join(' | ')}`).toEqual([])

    // 5) 真实模型任务: 关掉首次进入的测试声明弹层, textarea 作曲器输入 + Send 按钮
    try {
      await page.getByRole('button', { name: /Continue|继续/i }).first().click({ timeout: 8_000 })
    } catch { /* 非首次进入无弹层 */ }
    const composer = page.locator('textarea').first()
    await composer.waitFor({ timeout: 30_000 })
    await composer.click()
    await composer.fill('Reply with exactly: E2E-BROWSER-OK')
    const send = page.getByRole('button', { name: /Send message|发送/i })
    await expect(send).toBeEnabled({ timeout: 15_000 })
    await send.click()

    await expect(page.getByText('E2E-BROWSER-OK').first()).toBeVisible({ timeout: 180_000 })
  } finally {
    await deleteSession(sid)
  }
})
