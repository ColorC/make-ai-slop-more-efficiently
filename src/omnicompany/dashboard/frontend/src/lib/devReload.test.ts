import { beforeEach, describe, expect, it } from 'vitest'
import { hasLivePtySurface, shouldDeferUiReload } from './devReload'

describe('devReload PTY protection', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      value: 'visible',
    })
  })

  it('still detects a connected live PTY surface', () => {
    document.body.innerHTML = '<div data-cc-session-id="pty-1" data-cc-session-alive="true" data-cc-session-connected="true"></div>'
    expect(hasLivePtySurface()).toBe(true)
    expect(shouldDeferUiReload()).toBe(false)
  })

  it('does not block reload for reconnecting, ended, or absent PTY surfaces', () => {
    document.body.innerHTML = '<div data-cc-session-id="pty-1" data-cc-session-alive="true" data-cc-session-connected="false"></div>'
    expect(hasLivePtySurface()).toBe(false)
    document.body.innerHTML = '<div data-cc-session-id="pty-1" data-cc-session-alive="false" data-cc-session-connected="true"></div>'
    expect(hasLivePtySurface()).toBe(false)
    document.body.innerHTML = '<main>ordinary page</main>'
    expect(hasLivePtySurface()).toBe(false)
  })

  it('defers a hidden browser tab instead of reloading every view together', () => {
    document.body.innerHTML = '<div data-cc-session-id="pty-1" data-cc-session-alive="true" data-cc-session-connected="true"></div>'
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      value: 'hidden',
    })
    expect(shouldDeferUiReload()).toBe(true)
  })
})
