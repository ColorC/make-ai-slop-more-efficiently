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

  it('defers automatic UI reload while a connected live PTY surface is mounted', () => {
    document.body.innerHTML = '<div data-cc-session-id="pty-1" data-cc-session-alive="true" data-cc-session-connected="true"></div>'
    expect(hasLivePtySurface()).toBe(true)
  })

  it('does not block reload for reconnecting, ended, or absent PTY surfaces', () => {
    document.body.innerHTML = '<div data-cc-session-id="pty-1" data-cc-session-alive="true" data-cc-session-connected="false"></div>'
    expect(hasLivePtySurface()).toBe(false)
    document.body.innerHTML = '<div data-cc-session-id="pty-1" data-cc-session-alive="false" data-cc-session-connected="true"></div>'
    expect(hasLivePtySurface()).toBe(false)
    document.body.innerHTML = '<main>ordinary page</main>'
    expect(hasLivePtySurface()).toBe(false)
  })

  it('allows a hidden browser tab to update and release its PTY', () => {
    document.body.innerHTML = '<div data-cc-session-id="pty-1" data-cc-session-alive="true" data-cc-session-connected="true"></div>'
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      value: 'hidden',
    })
    expect(shouldDeferUiReload()).toBe(false)
  })
})
