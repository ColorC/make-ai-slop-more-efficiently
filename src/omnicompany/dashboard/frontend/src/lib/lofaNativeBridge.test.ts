import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  DASHBOARD_API_SCHEMA_VERSION,
  getLofaBridgeState,
  installLofaNativeBridge,
  LOFA_BOOTSTRAP_REQUEST,
  LOFA_BOOTSTRAP_RESPONSE,
  LOFA_BRIDGE_PROTOCOL,
  LOFA_MIN_NATIVE_BRIDGE_VERSION,
  LOFA_NATIVE_REQUEST,
  LOFA_NATIVE_RESULT,
  requestLofaNative,
} from './lofaNativeBridge'

describe('LOFA native bridge client', () => {
  const originalUrl = window.location.href

  beforeEach(() => {
    vi.useFakeTimers()
    window.history.replaceState({}, '', '/?lofaRemoteWeb=1&client=lofa')
    vi.spyOn(window, 'fetch').mockResolvedValue(new Response(JSON.stringify({ ui: 'ui-cl-1400000' }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }))
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.useRealTimers()
    window.history.replaceState({}, '', originalUrl)
    document.getElementById('lofa-native-compatibility-notice')?.remove()
  })

  it('sends versioned bootstrap metadata and accepts only the parent response', async () => {
    const post = vi.spyOn(window.parent, 'postMessage')
    const dispose = installLofaNativeBridge(window)
    await vi.advanceTimersByTimeAsync(1)
    const request = post.mock.calls.find(([value]) => value?.type === LOFA_BOOTSTRAP_REQUEST)?.[0]
    expect(request).toMatchObject({
      protocol: LOFA_BRIDGE_PROTOCOL,
      bootstrap: {
        frontend_build: 'ui-cl-1400000',
        api_schema_version: DASHBOARD_API_SCHEMA_VERSION,
        min_native_bridge_version: LOFA_MIN_NATIVE_BRIDGE_VERSION,
      },
    })

    window.dispatchEvent(new MessageEvent('message', {
      source: window,
      data: {
        type: LOFA_BOOTSTRAP_RESPONSE,
        protocol: LOFA_BRIDGE_PROTOCOL,
        request_id: request.request_id,
        native_bridge_version: 1,
        app_version: '1.2.3',
        capabilities: ['shell.open-settings', 'shell.open-settings'],
      },
    }))
    expect(getLofaBridgeState()).toEqual({
      status: 'ready',
      nativeBridgeVersion: 1,
      appVersion: '1.2.3',
      capabilities: ['shell.open-settings'],
    })
    dispose()
  })

  it('refuses undeclared actions and resolves a declared allowlisted request', async () => {
    const post = vi.spyOn(window.parent, 'postMessage')
    const dispose = installLofaNativeBridge(window)
    await vi.advanceTimersByTimeAsync(1)
    const bootstrap = post.mock.calls.find(([value]) => value?.type === LOFA_BOOTSTRAP_REQUEST)?.[0]
    window.dispatchEvent(new MessageEvent('message', {
      source: window,
      data: {
        type: LOFA_BOOTSTRAP_RESPONSE,
        protocol: LOFA_BRIDGE_PROTOCOL,
        request_id: bootstrap.request_id,
        native_bridge_version: 1,
        capabilities: ['device.status'],
      },
    }))

    await expect(requestLofaNative('eval', { script: 'danger' })).rejects.toThrow('capability is unavailable')
    const resultPromise = requestLofaNative('device.status')
    const request = post.mock.calls.find(([value]) => value?.type === LOFA_NATIVE_REQUEST)?.[0]
    expect(request).toMatchObject({ action: 'device.status', args: {} })
    window.dispatchEvent(new MessageEvent('message', {
      source: window,
      data: {
        type: LOFA_NATIVE_RESULT,
        protocol: LOFA_BRIDGE_PROTOCOL,
        request_id: request.request_id,
        ok: true,
        result: { paired: true },
      },
    }))
    await expect(resultPromise).resolves.toEqual({ paired: true })
    dispose()
  })

  it('degrades an old or missing APK bridge without blocking Dashboard', async () => {
    const post = vi.spyOn(window.parent, 'postMessage')
    const dispose = installLofaNativeBridge(window, 100)
    await vi.advanceTimersByTimeAsync(1)
    const request = post.mock.calls.find(([value]) => value?.type === LOFA_BOOTSTRAP_REQUEST)?.[0]
    window.dispatchEvent(new MessageEvent('message', {
      source: window,
      data: {
        type: LOFA_BOOTSTRAP_RESPONSE,
        protocol: LOFA_BRIDGE_PROTOCOL,
        request_id: request.request_id,
        native_bridge_version: 0,
        capabilities: [],
      },
    }))
    expect(getLofaBridgeState().status).toBe('outdated')
    expect(document.getElementById('lofa-native-compatibility-notice')?.textContent).toContain('更新 APK')
    dispose()

    const disposeMissing = installLofaNativeBridge(window, 100)
    await vi.advanceTimersByTimeAsync(101)
    expect(getLofaBridgeState().status).toBe('unavailable')
    expect(document.getElementById('lofa-native-compatibility-notice')?.textContent).toContain('原生桥未响应')
    disposeMissing()
  })

  it('publishes the LOFA native page carrier after capability negotiation', async () => {
    const post = vi.spyOn(window.parent, 'postMessage')
    const dispose = installLofaNativeBridge(window)
    await vi.advanceTimersByTimeAsync(1)
    const bootstrap = post.mock.calls.find(([value]) => value?.type === LOFA_BOOTSTRAP_REQUEST)?.[0]
    window.dispatchEvent(new MessageEvent('message', {
      source: window,
      data: {
        type: LOFA_BOOTSTRAP_RESPONSE,
        protocol: LOFA_BRIDGE_PROTOCOL,
        request_id: bootstrap.request_id,
        native_bridge_version: 2,
        capabilities: [
          'browser.open-external',
          'browser.webview.open',
          'browser.webview.update',
          'browser.webview.reload',
          'browser.webview.close',
        ],
      },
    }))
    expect(window.__OMNICOMPANY_HOST__?.runtime).toBe('lofa')

    const opened = window.__OMNICOMPANY_HOST__!.openExternalWebview!({
      id: 'web_native',
      url: 'https://example.com/no-frames',
      profile: 'human',
      bounds: { x: 1, y: 2, width: 300, height: 200 },
      visible: true,
    })
    const request = post.mock.calls.find(([value]) => value?.type === LOFA_NATIVE_REQUEST)?.[0]
    expect(request).toMatchObject({
      action: 'browser.webview.open',
      args: {
        id: 'web_native',
        url: 'https://example.com/no-frames',
        pixelRatio: window.devicePixelRatio,
      },
    })
    window.dispatchEvent(new MessageEvent('message', {
      source: window,
      data: {
        type: LOFA_NATIVE_RESULT,
        protocol: LOFA_BRIDGE_PROTOCOL,
        request_id: request.request_id,
        ok: true,
        result: { label: 'web_native' },
      },
    }))
    await expect(opened).resolves.toBe('web_native')
    dispose()
    expect(window.__OMNICOMPANY_HOST__).toBeUndefined()
  })
})
