import { afterEach, describe, expect, it, vi } from 'vitest'
import { nativeWebviewIsOccluded } from './nativeWebviewOcclusion'

function bounds(left: number, top: number, width: number, height: number): DOMRect {
  return {
    x: left,
    y: top,
    left,
    top,
    right: left + width,
    bottom: top + height,
    width,
    height,
    toJSON: () => ({}),
  }
}

describe('native WebView occlusion', () => {
  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('detects Dashboard menus and intersecting Dockview floating groups', () => {
    const surface = document.createElement('div')
    surface.getBoundingClientRect = vi.fn(() => bounds(100, 100, 500, 400))
    document.body.appendChild(surface)

    const menu = document.createElement('div')
    menu.setAttribute('data-omni-native-webview-overlay', 'true')
    menu.getBoundingClientRect = vi.fn(() => bounds(200, 150, 180, 220))
    document.body.appendChild(menu)
    expect(nativeWebviewIsOccluded(surface)).toBe(true)

    menu.remove()
    const floating = document.createElement('div')
    floating.className = 'dv-resize-container'
    floating.getBoundingClientRect = vi.fn(() => bounds(300, 200, 240, 200))
    document.body.appendChild(floating)
    expect(nativeWebviewIsOccluded(surface)).toBe(true)
  })

  it('ignores non-overlapping and owning floating groups', () => {
    const floating = document.createElement('div')
    floating.className = 'dv-resize-container'
    floating.getBoundingClientRect = vi.fn(() => bounds(100, 100, 500, 400))
    const surface = document.createElement('div')
    surface.getBoundingClientRect = vi.fn(() => bounds(120, 140, 460, 340))
    floating.appendChild(surface)
    document.body.appendChild(floating)
    expect(nativeWebviewIsOccluded(surface)).toBe(false)

    const distant = document.createElement('div')
    distant.className = 'dv-resize-container'
    distant.getBoundingClientRect = vi.fn(() => bounds(700, 100, 200, 200))
    document.body.appendChild(distant)
    expect(nativeWebviewIsOccluded(surface)).toBe(false)
  })
})
