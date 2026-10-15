import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  inspectClientId,
  inspectHostIdentity,
  inspectHostIdentityWithInfo,
  inspectHostRuntime,
  dispatchInspectKey,
  dispatchInspectType,
  runInspectCommand,
  runInspectCommandAsync,
  startSerialPolling,
} from './clientInspect'
import { registerTerminalInspect } from '../entities/cc_session/terminalInspect'

describe('client inspect helpers', () => {
  beforeEach(() => {
    window.localStorage.clear()
    delete (window as Window & { __OMNICOMPANY_HOST__?: unknown }).__OMNICOMPANY_HOST__
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('reuses a stored client id', () => {
    const first = inspectClientId()
    const second = inspectClientId()
    expect(first).toMatch(/^[a-f0-9]{32}$/)
    expect(second).toBe(first)
  })

  it('names the desktop host when the Tauri bridge is present', () => {
    expect(inspectHostRuntime()).toBe('web')
    ;(window as Window & { __OMNICOMPANY_HOST__?: { runtime: string } }).__OMNICOMPANY_HOST__ = {
      runtime: 'desktop',
    }
    expect(inspectHostRuntime()).toBe('desktop')
  })

  it('only projects explicit desktop identity facts', () => {
    ;(window as Window & { __OMNICOMPANY_HOST__?: unknown }).__OMNICOMPANY_HOST__ = {
      runtime: 'desktop', hostname: 'desktop-node', installId: 'install-1', connectionMethod: 'paired-runtime',
    }
    expect(inspectHostIdentity()).toEqual({
      reportedHostname: 'desktop-node', reportedInstallId: 'install-1', connectionMethod: 'paired-runtime',
    })
  })

  it('merges native host self-report from the existing Tauri hostInfo bridge', async () => {
    ;(window as Window & { __OMNICOMPANY_HOST__?: unknown }).__OMNICOMPANY_HOST__ = {
      runtime: 'desktop',
      hostInfo: vi.fn(async () => ({
        hostname: 'DESKTOP-REMOTE', installId: 'desktop-node-1', connectionMethod: 'paired-remote-node-poll',
      })),
    }
    await expect(inspectHostIdentityWithInfo()).resolves.toEqual({
      reportedHostname: 'DESKTOP-REMOTE',
      reportedInstallId: 'desktop-node-1',
      connectionMethod: 'paired-remote-node-poll',
    })
  })

  it('uses a native WebView PNG when the desktop host bridge supplies one', async () => {
    ;(window as Window & { __OMNICOMPANY_HOST__?: unknown }).__OMNICOMPANY_HOST__ = {
      runtime: 'desktop',
      captureScreenshot: vi.fn(async () => 'data:image/png;base64,cmVhbC1wbmc='),
    }
    const screenshot = await runInspectCommandAsync({ id: '1', kind: 'screenshot' })
    expect(screenshot.ok).toBe(true)
    expect((screenshot.result as { screenshot?: { source?: string } }).screenshot?.source).toBe('desktop-webview2')
  })

  it('preserves native screenshot failure evidence when page fallback is unsupported', async () => {
    ;(window as Window & { __OMNICOMPANY_HOST__?: unknown }).__OMNICOMPANY_HOST__ = {
      runtime: 'desktop',
      captureScreenshot: vi.fn(async () => { throw new Error('webview unavailable') }),
    }
    const screenshot = await runInspectCommandAsync({ id: '1', kind: 'screenshot' })
    expect(screenshot.ok).toBe(false)
    expect(screenshot.error).toBe('unsupported')
    expect((screenshot.result as { nativeCaptureError?: string }).nativeCaptureError).toBe('webview unavailable')
  })

  it('runs info and eval commands without throwing', () => {
    const info = runInspectCommand({ id: '1', kind: 'info' })
    expect(info.ok).toBe(true)
    expect((info.result as { href?: string }).href).toContain('http')
    const evaluated = runInspectCommand({
      id: '2',
      kind: 'eval',
      payload: { expression: '1 + 2' },
    })
    expect(evaluated).toEqual({ ok: true, result: 3 })
  })

  it('routes inspect type and named keys through the visible terminal adapter', () => {
    const calls: Array<string> = []
    const input = document.createElement('textarea')
    input.className = 'xterm-helper-textarea'
    document.body.appendChild(input)
    input.focus()
    const unregister = registerTerminalInspect('sid-terminal', {
      sessionId: 'sid-terminal',
      provider: 'claude_code',
      visible: () => true,
      snapshot: () => ({ sessionId: 'sid-terminal', provider: 'claude_code', visible: true, cols: 80, rows: 24, lines: [], canvasDataUrl: null }),
      input: {
        focus: vi.fn(),
        ownsActiveElement: (element) => element === input,
        paste: (text) => calls.push(`paste:${text}`),
        key: (key) => calls.push(`key:${key}`),
      },
    })
    expect(dispatchInspectType('hello')).toMatchObject({ target: 'terminal', sessionId: 'sid-terminal' })
    expect(dispatchInspectKey('ArrowLeft')).toMatchObject({ target: 'terminal', sessionId: 'sid-terminal' })
    expect(calls).toEqual(['paste:hello', 'key:ArrowLeft'])
    unregister()
    input.remove()
    document.body.focus()
  })

  it('routes to the focused terminal when multiple visible terminals are mounted', () => {
    const first = document.createElement('textarea')
    const second = document.createElement('textarea')
    document.body.append(first, second)
    second.focus()
    const calls: string[] = []
    const unregisterFirst = registerTerminalInspect('sid-first', {
      sessionId: 'sid-first', provider: 'codex', visible: () => true,
      snapshot: () => ({ sessionId: 'sid-first', provider: 'codex', visible: true, cols: 80, rows: 24, lines: [], canvasDataUrl: null }),
      input: { focus: vi.fn(), ownsActiveElement: (element) => element === first, paste: (text) => calls.push(`first:${text}`), key: vi.fn() },
    })
    const unregisterSecond = registerTerminalInspect('sid-second', {
      sessionId: 'sid-second', provider: 'codex', visible: () => true,
      snapshot: () => ({ sessionId: 'sid-second', provider: 'codex', visible: true, cols: 80, rows: 24, lines: [], canvasDataUrl: null }),
      input: { focus: vi.fn(), ownsActiveElement: (element) => element === second, paste: (text) => calls.push(`second:${text}`), key: vi.fn() },
    })
    expect(dispatchInspectType('focused')).toMatchObject({ sessionId: 'sid-second' })
    expect(calls).toEqual(['second:focused'])
    unregisterSecond()
    unregisterFirst()
    first.remove()
    second.remove()
  })

  it('keeps ordinary textarea input and rejects missing terminal input', () => {
    const input = document.createElement('textarea')
    document.body.appendChild(input)
    input.focus()
    expect(dispatchInspectType('hello')).toMatchObject({ target: 'textarea' })
    expect(input.value).toBe('hello')
    input.remove()
    expect(runInspectCommand({ id: 'missing', kind: 'key', payload: { key: 'ArrowLeft' } })).toEqual({
      ok: false,
      error: 'no visible terminal input adapter',
    })
  })

  it('does not report success for an unsupported terminal key', () => {
    const input = document.createElement('textarea')
    input.className = 'xterm-helper-textarea'
    document.body.appendChild(input)
    input.focus()
    const unregister = registerTerminalInspect('sid-unknown-key', {
      sessionId: 'sid-unknown-key', provider: 'codex', visible: () => true,
      snapshot: () => ({ sessionId: 'sid-unknown-key', provider: 'codex', visible: true, cols: 80, rows: 24, lines: [], canvasDataUrl: null }),
      input: {
        focus: vi.fn(),
        ownsActiveElement: (element) => element === input,
        key: (key) => { throw new Error(`unsupported terminal inspect key: ${key}`) },
        paste: vi.fn(),
      },
    })
    const result = runInspectCommand({ id: 'unknown', kind: 'key', payload: { key: 'F99' } })
    expect(result.ok).toBe(false)
    expect(result.error).toBe('unsupported terminal inspect key: F99')
    unregister()
    input.remove()
  })

  it('does not report a page screenshot when no image was captured', () => {
    const screenshot = runInspectCommand({ id: '1', kind: 'screenshot' })
    expect(screenshot.ok).toBe(false)
    expect(screenshot.error).toBe('unsupported')
  })

  it('never overlaps polling when a request is slow', async () => {
    vi.useFakeTimers()
    let finishFirst: (() => void) | undefined
    const task = vi.fn(() => new Promise<void>((resolve) => { finishFirst = resolve }))
    const stop = startSerialPolling(task, 750)

    await vi.advanceTimersByTimeAsync(750)
    expect(task).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(7_500)
    expect(task).toHaveBeenCalledTimes(1)

    finishFirst?.()
    await Promise.resolve()
    await vi.advanceTimersByTimeAsync(750)
    expect(task).toHaveBeenCalledTimes(2)
    stop()
  })
})
