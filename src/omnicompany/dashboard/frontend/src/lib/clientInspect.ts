import { getTerminalInspectInput, listTerminalInspect } from '../entities/cc_session/terminalInspect'
import { usePanels } from '../stores/panelsStore'

const HELLO_MS = 8_000
const POLL_MS = 750
const ID_KEY = 'omnicompany.inspect.client.v1'

export type InspectCommand = {
  id: string
  kind: 'screenshot' | 'eval' | 'click' | 'type' | 'key' | 'info'
  payload?: Record<string, unknown>
}

type InspectHostBridge = {
  runtime?: string
  hostname?: string
  installId?: string
  connectionMethod?: string
  hostInfo?: () => unknown
  captureScreenshot?: () => unknown
}

function inspectHostBridge(win: Window = window): InspectHostBridge | undefined {
  return (win as Window & { __OMNICOMPANY_HOST__?: InspectHostBridge }).__OMNICOMPANY_HOST__
}

export function inspectClientId(storage: Storage = window.localStorage): string {
  try {
    const existing = (storage.getItem(ID_KEY) ?? '').trim()
    if (/^[a-f0-9]{32}$/.test(existing)) return existing
  } catch { /* private mode */ }
  const bytes = new Uint8Array(16)
  if (globalThis.crypto?.getRandomValues) globalThis.crypto.getRandomValues(bytes)
  else for (let i = 0; i < 16; i += 1) bytes[i] = Math.floor(Math.random() * 256)
  const created = Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('')
  try { storage.setItem(ID_KEY, created) } catch { /* */ }
  return created
}

function inspectConnectionId(): string {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
  const bytes = new Uint8Array(16)
  if (globalThis.crypto?.getRandomValues) globalThis.crypto.getRandomValues(bytes)
  else for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256)
  return Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('')
}

export function inspectHostRuntime(win: Window = window): 'desktop' | 'web' {
  const host = inspectHostBridge(win)
  return host?.runtime === 'desktop' ? 'desktop' : 'web'
}

export function inspectHostIdentity(win: Window = window): Record<string, string> {
  const host = inspectHostBridge(win)
  const identity: Record<string, string> = {}
  if (host?.hostname?.trim()) identity.reportedHostname = host.hostname.trim()
  if (host?.installId?.trim()) identity.reportedInstallId = host.installId.trim()
  if (host?.connectionMethod?.trim()) identity.connectionMethod = host.connectionMethod.trim()
  return identity
}

export async function inspectHostIdentityWithInfo(win: Window = window): Promise<Record<string, string>> {
  const identity = inspectHostIdentity(win)
  const host = inspectHostBridge(win)
  if (!host?.hostInfo) return identity
  try {
    const value = await Promise.resolve(host.hostInfo()) as {
      hostname?: unknown
      installId?: unknown
      connectionMethod?: unknown
    } | null
    if (value && typeof value === 'object') {
      if (!identity.reportedHostname && typeof value.hostname === 'string' && value.hostname.trim()) {
        identity.reportedHostname = value.hostname.trim()
      }
      if (!identity.reportedInstallId && typeof value.installId === 'string' && value.installId.trim()) {
        identity.reportedInstallId = value.installId.trim()
      }
      if (!identity.connectionMethod && typeof value.connectionMethod === 'string' && value.connectionMethod.trim()) {
        identity.connectionMethod = value.connectionMethod.trim()
      }
    }
  } catch { /* host command unavailable while the page is reconnecting */ }
  return identity
}

function visibleText(limit = 4_000): string {
  const root = document.querySelector('[data-omni-dashboard-shell], .dv-dockview, body')
  const text = (root?.textContent || '').replace(/\s+/g, ' ').trim()
  return text.slice(0, limit)
}

function captureCanvases(limit = 6): Array<{ width: number; height: number; dataUrl: string }> {
  const out: Array<{ width: number; height: number; dataUrl: string }> = []
  for (const canvas of Array.from(document.querySelectorAll('canvas'))) {
    if (!(canvas instanceof HTMLCanvasElement)) continue
    if (canvas.width < 8 || canvas.height < 8) continue
    try {
      out.push({
        width: canvas.width,
        height: canvas.height,
        dataUrl: canvas.toDataURL('image/png'),
      })
    } catch { /* tainted */ }
    if (out.length >= limit) break
  }
  return out
}

export function collectInspectInfo(): Record<string, unknown> {
  const panels = usePanels.getState()
  return {
    href: location.href,
    title: document.title,
    hostRuntime: inspectHostRuntime(),
    hostIdentity: inspectHostIdentity(),
    viewport: { width: window.innerWidth, height: window.innerHeight },
    visibility: document.visibilityState,
    activeTabId: panels.activeId,
    tabs: panels.tabs.map((tab) => ({
      id: tab.id,
      title: tab.title,
      type: tab.ref.type,
      entityId: tab.ref.id,
      active: tab.id === panels.activeId,
    })),
    terminals: listTerminalInspect().map((item) => ({
      ...item,
      canvasDataUrl: item.canvasDataUrl ? `[png ${item.canvasDataUrl.length}]` : null,
      lineCount: item.lines.length,
      preview: item.lines.slice(-24),
    })),
  }
}

export function collectInspectScreenshot(): Record<string, unknown> {
  return {
    ...collectInspectInfo(),
    pageText: visibleText(),
    canvases: captureCanvases(),
    terminals: listTerminalInspect(),
  }
}

export function dispatchInspectClick(x: number, y: number): Record<string, unknown> {
  const target = document.elementFromPoint(x, y)
  const eventInit: MouseEventInit = {
    bubbles: true,
    cancelable: true,
    view: window,
    clientX: x,
    clientY: y,
    button: 0,
  }
  for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'] as const) {
    target?.dispatchEvent(new MouseEvent(type, eventInit))
  }
  return {
    x,
    y,
    tag: target instanceof Element ? target.tagName.toLowerCase() : null,
    text: (target?.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 160),
  }
}

export function dispatchInspectType(text: string): Record<string, unknown> {
  const active = document.activeElement
  const terminal = getTerminalInspectInput(active)
  const looksLikeTerminal = active instanceof HTMLElement && Boolean(active.closest('.xterm, .xterm-helper-textarea'))
  if (terminal?.ownsActiveElement?.(active)) {
    terminal.paste(text)
    return { target: 'terminal', sessionId: terminal.sessionId, length: text.length }
  }
  if (looksLikeTerminal) throw new Error('no matching visible terminal input adapter')
  if (active instanceof HTMLInputElement || active instanceof HTMLTextAreaElement) {
    const start = active.selectionStart ?? active.value.length
    const end = active.selectionEnd ?? active.value.length
    active.setRangeText(text, start, end, 'end')
    active.dispatchEvent(new Event('input', { bubbles: true }))
    return { target: active.tagName.toLowerCase(), length: text.length }
  }
  if (active instanceof HTMLElement && active.isContentEditable) {
    active.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, data: text, inputType: 'insertText' }))
    active.dispatchEvent(new InputEvent('input', { bubbles: true, data: text, inputType: 'insertText' }))
    return { target: 'contenteditable', length: text.length }
  }
  throw new Error('no visible terminal input adapter and no focused editable target')
}

export function dispatchInspectKey(key: string): Record<string, unknown> {
  const active = document.activeElement
  const terminal = getTerminalInspectInput(active)
  if (terminal) {
    terminal.key(key)
    return { key, target: 'terminal', sessionId: terminal.sessionId }
  }
  if (active instanceof HTMLElement && active.closest('.xterm, .xterm-helper-textarea')) {
    throw new Error('no matching visible terminal input adapter')
  }
  if (active === document.body) throw new Error('no visible terminal input adapter')
  if (!(active instanceof HTMLElement)) throw new Error('no visible terminal input adapter')
  const target = active
  const init: KeyboardEventInit = { key, bubbles: true, cancelable: true }
  target.dispatchEvent(new KeyboardEvent('keydown', init))
  target.dispatchEvent(new KeyboardEvent('keyup', init))
  return { key, target: target instanceof Element ? target.tagName.toLowerCase() : null }
}

export function runInspectCommand(command: InspectCommand): { ok: boolean; result?: unknown; error?: string } {
  try {
    if (command.kind === 'info') return { ok: true, result: collectInspectInfo() }
    if (command.kind === 'screenshot') {
      const result = collectInspectScreenshot()
      const canvases = result.canvases as Array<unknown>
      // The page adapter can only return actual PNG data URLs from canvases.
      // DOM text/terminal metadata is useful diagnostics, but is not a
      // screenshot and must never be reported as one.
      if (!Array.isArray(canvases) || canvases.length === 0) {
        return { ok: false, result, error: 'unsupported' }
      }
      return { ok: true, result }
    }
    if (command.kind === 'eval') {
      const expression = String(command.payload?.expression ?? '')
      // Isolated Function, not eval(), so inspect stays a data channel.
      const value = Function(`"use strict"; return (${expression})`)()
      return { ok: true, result: value }
    }
    if (command.kind === 'click') {
      return {
        ok: true,
        result: dispatchInspectClick(Number(command.payload?.x), Number(command.payload?.y)),
      }
    }
    if (command.kind === 'type') {
      return { ok: true, result: dispatchInspectType(String(command.payload?.text ?? '')) }
    }
    if (command.kind === 'key') {
      return { ok: true, result: dispatchInspectKey(String(command.payload?.key ?? '')) }
    }
    return { ok: false, error: 'unknown_kind' }
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) }
  }
}

export async function runInspectCommandAsync(command: InspectCommand): Promise<{ ok: boolean; result?: unknown; error?: string }> {
  if (command.kind !== 'screenshot') return runInspectCommand(command)
  const host = inspectHostBridge()
  let nativeCaptureError: string | undefined
  if (host?.captureScreenshot) {
    try {
      const dataUrl = await Promise.resolve(host.captureScreenshot())
      if (typeof dataUrl === 'string' && dataUrl.startsWith('data:image/png;base64,')) {
        return {
          ok: true,
          result: {
            ...collectInspectInfo(),
            screenshot: { dataUrl, source: 'desktop-webview2' },
          },
        }
      }
    } catch (error) {
      nativeCaptureError = error instanceof Error ? error.message : String(error)
    }
  }
  const fallback = runInspectCommand(command)
  if (!nativeCaptureError) return fallback
  const fallbackResult = fallback.result && typeof fallback.result === 'object'
    ? { ...(fallback.result as Record<string, unknown>), nativeCaptureError }
    : { nativeCaptureError }
  // Keep the page adapter's honest `unsupported` error while exposing why the
  // preferred native path was unavailable to the controller.
  return { ...fallback, result: fallbackResult }
}

async function postJson(path: string, body: unknown): Promise<void> {
  await fetch(path, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function startSerialPolling(
  task: () => void | Promise<void>,
  intervalMs: number | (() => number),
  timers: Pick<Window, 'setTimeout' | 'clearTimeout'> = window,
): () => void {
  let stopped = false
  let timer: number | undefined
  const tick = async () => {
    if (stopped) return
    try {
      await task()
    } catch {
      // Inspect is diagnostic-only; a restart/network failure must neither
      // surface as an unhandled rejection nor stop future probes.
    } finally {
      const delay = typeof intervalMs === 'function' ? intervalMs() : intervalMs
      if (!stopped) timer = timers.setTimeout(() => { void tick() }, delay)
    }
  }
  const initialDelay = typeof intervalMs === 'function' ? intervalMs() : intervalMs
  timer = timers.setTimeout(() => { void tick() }, initialDelay)
  return () => {
    stopped = true
    if (timer !== undefined) timers.clearTimeout(timer)
  }
}

export function startClientInspect(): void {
  if (typeof window === 'undefined') return
  const win = window as Window & { __OMNI_CLIENT_INSPECT__?: unknown }
  if (win.__OMNI_CLIENT_INSPECT__) return
  const id = inspectClientId()
  // The client id is persistent for observation continuity; this id is
  // intentionally per page load so the server can requeue commands that were
  // in flight when WebView2 restarted, without duplicating slow commands on
  // periodic hello heartbeats.
  const connectionId = inspectConnectionId()
  const hello = async () => {
    const hostIdentity = await inspectHostIdentityWithInfo()
    await postJson('/api/clients/desktop/inspect/hello', {
      id,
      href: location.href,
      title: document.title,
      userAgent: navigator.userAgent,
      hostRuntime: inspectHostRuntime(),
      connectionId,
      ...hostIdentity,
    }).catch(() => {})
  }
  const poll = async () => {
    if (document.visibilityState === 'hidden') return
    try {
      const response = await fetch(`/api/clients/desktop/inspect/poll?id=${encodeURIComponent(id)}`, {
        credentials: 'same-origin',
        cache: 'no-store',
      })
      if (!response.ok) return
      const data = await response.json() as { commands?: InspectCommand[] }
      for (const command of data.commands || []) {
        const outcome = await runInspectCommandAsync(command)
        await postJson('/api/clients/desktop/inspect/result', {
          commandId: command.id,
          ok: outcome.ok,
          result: outcome.result,
          error: outcome.error,
        })
      }
    } catch { /* dashboard restart */ }
  }
  win.__OMNI_CLIENT_INSPECT__ = {
    id,
    run: runInspectCommand,
    info: collectInspectInfo,
    screenshot: collectInspectScreenshot,
  }
  hello()
  // Schedule the next request only after the previous one settles. Under a
  // stalled Dashboard, setInterval used to accumulate overlapping inspect
  // polls every 750 ms and starve the browser's HTTP/1.1 connection pool —
  // including the interactive terminal transport.
  // Inspect remains responsive even for an idle dashboard: remote diagnosis
  // must be able to wake or inspect a page without waiting for the sleep timer.
  startSerialPolling(hello, HELLO_MS)
  startSerialPolling(poll, POLL_MS)
}
