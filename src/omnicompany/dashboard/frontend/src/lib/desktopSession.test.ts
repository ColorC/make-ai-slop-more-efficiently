import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  desktopInstanceId,
  desktopWebviewSession,
  ensureDesktopSession,
  isWorkspaceGatewayUrl,
} from './desktopSession'

describe('desktopSession', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  it('keeps one non-secret instance identity in the persistent WebView profile', () => {
    const first = desktopInstanceId(window.localStorage)
    const second = desktopInstanceId(window.localStorage)

    expect(first).toMatch(/^[a-f0-9]{32}$/)
    expect(second).toBe(first)
  })

  it('replaces malformed persisted identity', () => {
    window.localStorage.setItem('omnicompany.desktop.instance.v1', 'not-an-instance')
    expect(desktopInstanceId(window.localStorage)).toMatch(/^[a-f0-9]{32}$/)
  })

  it('asks the same origin to persist the authenticated desktop session', async () => {
    const request = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => (
      Response.json({
        ok: true,
        webview_session: {
          token: 'signed-payload.signed-value',
          expires_at: Math.floor(Date.now() / 1000) + 3600,
        },
      })
    ))

    await expect(ensureDesktopSession(request, window.localStorage)).resolves.toBe(true)
    expect(request).toHaveBeenCalledOnce()
    expect(request).toHaveBeenCalledWith('/api/desktop/session', expect.objectContaining({
      method: 'POST',
      credentials: 'same-origin',
      headers: expect.objectContaining({
        'X-Omnicompany-Desktop': '1',
      }),
    }))
    const options = request.mock.calls[0]?.[1]
    expect(JSON.parse(String(options?.body)).instance_id).toMatch(/^[a-f0-9]{32}$/)
  })

  it('reports a refused enrollment without changing the UI', async () => {
    const request = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => (
      new Response(null, { status: 403 })
    ))
    await expect(ensureDesktopSession(request, window.localStorage)).resolves.toBe(false)
  })

  it('keeps the native WebView session in memory and reuses it before expiry', async () => {
    const expiresAt = Math.floor(Date.now() / 1000) + 3600
    const request = vi.fn(async () => Response.json({
      ok: true,
      webview_session: {
        token: 'webview-payload.webview-signature',
        expires_at: expiresAt,
      },
    }))

    await ensureDesktopSession(request, window.localStorage)
    await expect(desktopWebviewSession(request, window.localStorage)).resolves.toEqual({
      token: 'webview-payload.webview-signature',
      expiresAt,
    })
    expect(request).toHaveBeenCalledOnce()
    expect(window.localStorage.getItem('webview-payload.webview-signature')).toBeNull()
  })

  it('limits native session bridging to HTTP and HTTPS workspace gateways', () => {
    expect(isWorkspaceGatewayUrl('https://192.0.2.10:12444/app/workboard/')).toBe(true)
    expect(isWorkspaceGatewayUrl('http://192.0.2.10:8210/spark-webtwin/')).toBe(true)
    expect(isWorkspaceGatewayUrl('https://example.com/')).toBe(false)
    expect(isWorkspaceGatewayUrl('file:///C:/secret.txt')).toBe(false)
    expect(isWorkspaceGatewayUrl('not a URL')).toBe(false)
  })
})
