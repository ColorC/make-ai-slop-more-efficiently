import { afterEach, describe, expect, it } from 'vitest'
import { WINDOW_CORNER_ATTR, markWindowCornerHeaders } from './desktopWindowChrome'

function header(className: string, rect: Partial<DOMRect>): HTMLElement {
  const element = document.createElement('div')
  element.className = className
  element.getBoundingClientRect = () => ({ top: 0, left: 0, right: 0, bottom: 0, width: 100, height: 30, x: 0, y: 0, toJSON() {}, ...rect }) as DOMRect
  document.body.appendChild(element)
  return element
}

afterEach(() => { document.body.innerHTML = '' })

describe('markWindowCornerHeaders', () => {
  it('marks only the header touching the top-right corner', () => {
    const left = header('dv-tabs-and-actions-container', { top: 0, right: 700 })
    const corner = header('dv-tabs-and-actions-container', { top: 0, right: 1440 })
    const lower = header('dv-tabs-and-actions-container', { top: 400, right: 1440 })
    expect(markWindowCornerHeaders(document, 1440)).toBe(1)
    expect(corner.hasAttribute(WINDOW_CORNER_ATTR)).toBe(true)
    expect(left.hasAttribute(WINDOW_CORNER_ATTR)).toBe(false)
    expect(lower.hasAttribute(WINDOW_CORNER_ATTR)).toBe(false)
  })

  it('unmarks a header that no longer reaches the corner', () => {
    const nav = header('cccp-nav', { top: 0, right: 1440 })
    markWindowCornerHeaders(document, 1440)
    nav.getBoundingClientRect = () => ({ top: 0, right: 900, width: 100 }) as DOMRect
    markWindowCornerHeaders(document, 1440)
    expect(nav.hasAttribute(WINDOW_CORNER_ATTR)).toBe(false)
  })
})
