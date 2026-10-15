import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  detachedDashboardUrl,
  isDetachedDashboardUrl,
  isOutsideViewport,
  openDetachedTabWindow,
} from './detachedTabWindow'

describe('detachedTabWindow', () => {
  const originalOpen = window.open

  afterEach(() => {
    window.open = originalOpen
  })

  it('only tears off after the drag leaves the current viewport', () => {
    expect(isOutsideViewport({ clientX: 200, clientY: 120 }, { width: 800, height: 600 })).toBe(false)
    expect(isOutsideViewport({ clientX: 801, clientY: 120 }, { width: 800, height: 600 })).toBe(true)
    expect(isOutsideViewport({ clientX: 0, clientY: 120 }, { width: 800, height: 600 })).toBe(true)
  })

  it('marks a same-origin Dashboard deeplink as a detached window request', () => {
    const url = detachedDashboardUrl(
      '/?open_type=cc_session&open_id=session-1&open_title=CLI',
      'https://dashboard.test:12443/',
    )
    expect(url).toBe(
      'https://dashboard.test:12443/?open_type=cc_session&open_id=session-1&open_title=CLI&omni_detached=1',
    )
    expect(isDetachedDashboardUrl(url, 'https://dashboard.test:12443/')).toBe(true)
    expect(isDetachedDashboardUrl(url, 'https://other.test/')).toBe(false)
  })

  it('reports success only when the browser or desktop host accepts the new window', () => {
    const child = { opener: window } as unknown as Window
    const nativeOpen = vi.fn(() => child)
    window.open = nativeOpen

    expect(openDetachedTabWindow({
      url: '/?open_type=cc_session&open_id=session-1',
      title: 'CLI',
      point: { clientX: 900, clientY: 300, screenX: 1300, screenY: 500 },
    })).toBe(true)
    expect(nativeOpen).toHaveBeenCalledWith(
      expect.stringContaining('omni_detached=1'),
      '_blank',
      expect.stringContaining('popup=yes'),
    )
    expect(child.opener).toBeNull()

    window.open = vi.fn(() => null)
    expect(openDetachedTabWindow({
      url: '/?open_type=cc_session&open_id=session-1',
      title: 'CLI',
      point: { clientX: 900, clientY: 300, screenX: 1300, screenY: 500 },
    })).toBe(false)
  })
})
