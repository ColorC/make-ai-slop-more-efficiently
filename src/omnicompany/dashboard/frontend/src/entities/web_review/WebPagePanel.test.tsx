import React from 'react'
import { act, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { DESKTOP_WINDOW_STATE_EVENT } from '../../lib/workspaceNavigation'
import WebPagePanel from './WebPagePanel'

describe('WebPagePanel embedding fallback', () => {
  beforeEach(() => {
    delete window.__OMNICOMPANY_HOST__
  })

  it('uses the official YouTube embed URL instead of framing the watch page', () => {
    render(<WebPagePanel target={{
      title: 'Video',
      url: 'https://www.youtube.com/watch?v=abcdefghijk&t=90',
    }} />)

    const frame = screen.getByTestId('web-page-frame')
    expect(frame.getAttribute('src'))
      .toBe('https://www.youtube.com/embed/abcdefghijk?start=90')
    expect(frame.getAttribute('referrerpolicy')).toBe('strict-origin-when-cross-origin')
    expect(screen.queryByTestId('web-page-external-fallback')).toBeNull()
  })

  it('does not keep an unsupported external page in a doomed iframe', () => {
    render(<WebPagePanel target={{ title: 'Docs', url: 'https://example.com/docs' }} />)

    expect(screen.queryByTestId('web-page-frame')).toBeNull()
    expect(screen.getByTestId('web-page-external-fallback')).not.toBeNull()
  })

  it('does not iframe a private service on another origin', () => {
    render(<WebPagePanel target={{ title: 'Protected LAN app', url: 'https://127.0.0.1:12444/app/demo/' }} />)

    expect(screen.queryByTestId('web-page-frame')).toBeNull()
    expect(screen.getByTestId('web-page-external-fallback')).not.toBeNull()
  })

  it('loads a legacy Workshop internal URL through the same-origin mobile mount', () => {
    render(<WebPagePanel target={{ title: 'Workshop', url: 'http://127.0.0.1:5460/canvas' }} />)

    const frame = screen.getByTestId('web-page-frame')
    expect(frame.getAttribute('src')).toBe(new URL('/bside/canvas', window.location.origin).href)
    expect(frame.getAttribute('allow')).toContain('clipboard-write')
    expect(screen.queryByTestId('web-page-external-fallback')).toBeNull()
  })

  it.each(['desktop', 'lofa'] as const)('keeps a %s native child WebView alive while its retained tab is hidden', async (runtime) => {
    const openExternalWebview = vi.fn(async () => 'external-test')
    const updateExternalWebview = vi.fn(async () => undefined)
    const closeExternalWebview = vi.fn(async () => undefined)
    window.__OMNICOMPANY_HOST__ = {
      runtime,
      openSystemUrl: vi.fn(),
      openExternalWebview,
      updateExternalWebview,
      closeExternalWebview,
    }

    const originalResizeObserver = globalThis.ResizeObserver
    const originalFetch = globalThis.fetch
    const originalRequestAnimationFrame = window.requestAnimationFrame
    const originalCancelAnimationFrame = window.cancelAnimationFrame
    const originalGetBoundingClientRect = HTMLElement.prototype.getBoundingClientRect
    let nextFrame = 1
    const frames = new Map<number, FrameRequestCallback>()
    globalThis.ResizeObserver = class ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    window.requestAnimationFrame = vi.fn((callback: FrameRequestCallback) => {
      const id = nextFrame++
      frames.set(id, callback)
      return id
    })
    window.cancelAnimationFrame = vi.fn((id: number) => { frames.delete(id) })
    HTMLElement.prototype.getBoundingClientRect = vi.fn(() => ({
      x: 10,
      y: 20,
      left: 10,
      top: 20,
      right: 650,
      bottom: 500,
      width: 640,
      height: 480,
      toJSON: () => ({}),
    }))
    globalThis.fetch = vi.fn(async () => Response.json({
      ok: true,
      webview_session: {
        token: 'native-payload.native-signature',
        expires_at: Math.floor(Date.now() / 1000) + 3600,
      },
    }))

    const flushFrames = async () => {
      await act(async () => {
        await Promise.resolve()
        const pending = [...frames.entries()]
        frames.clear()
        pending.forEach(([id, callback]) => callback(id))
      })
    }

    try {
      const target = {
        title: 'Workboard',
        url: 'https://127.0.0.1:12444/app/example-workboard/',
      }
      const view = render(<WebPagePanel target={target} panelVisible />)
      expect(screen.queryByTestId('web-page-frame')).toBeNull()
      expect(screen.getByTestId('web-page-native-surface')).not.toBeNull()
      await waitFor(() => expect(openExternalWebview).toHaveBeenCalledTimes(1))
      expect(openExternalWebview).toHaveBeenCalledWith(expect.objectContaining({
        visible: true,
        webviewSession: {
          token: 'native-payload.native-signature',
          expiresAt: expect.any(Number),
        },
      }))

      await flushFrames()
      expect(updateExternalWebview).toHaveBeenLastCalledWith(expect.objectContaining({ visible: true }))

      const menuBackdrop = document.createElement('div')
      menuBackdrop.setAttribute('data-omni-native-webview-overlay', 'true')
      document.body.appendChild(menuBackdrop)
      await act(async () => { await Promise.resolve() })
      await flushFrames()
      expect(updateExternalWebview).toHaveBeenLastCalledWith(expect.objectContaining({ visible: false }))

      menuBackdrop.remove()
      await act(async () => { await Promise.resolve() })
      await flushFrames()
      expect(updateExternalWebview).toHaveBeenLastCalledWith(expect.objectContaining({ visible: true }))

      view.rerender(<WebPagePanel target={target} panelVisible={false} />)
      await flushFrames()
      expect(openExternalWebview).toHaveBeenCalledTimes(1)
      expect(closeExternalWebview).not.toHaveBeenCalled()
      expect(updateExternalWebview).toHaveBeenLastCalledWith(expect.objectContaining({ visible: false }))

      view.rerender(<WebPagePanel target={target} panelVisible />)
      await flushFrames()
      expect(openExternalWebview).toHaveBeenCalledTimes(1)
      expect(closeExternalWebview).not.toHaveBeenCalled()
      expect(updateExternalWebview).toHaveBeenLastCalledWith(expect.objectContaining({ visible: true }))

      act(() => {
        window.dispatchEvent(new CustomEvent(DESKTOP_WINDOW_STATE_EVENT, {
          detail: { fullscreen: true, titlebarHeight: 0 },
        }))
      })
      await flushFrames()
      expect(openExternalWebview).toHaveBeenCalledTimes(1)

      view.unmount()
      await waitFor(() => expect(closeExternalWebview).toHaveBeenCalledTimes(1))
    } finally {
      globalThis.ResizeObserver = originalResizeObserver
      window.requestAnimationFrame = originalRequestAnimationFrame
      window.cancelAnimationFrame = originalCancelAnimationFrame
      HTMLElement.prototype.getBoundingClientRect = originalGetBoundingClientRect
      globalThis.fetch = originalFetch
      delete window.__OMNICOMPANY_HOST__
    }
  })

})
