import { describe, expect, it, vi } from 'vitest'
import { installTouchTabStripScroller } from './touchTabStripScroller'

function pointer(type: string, x: number, y = 10): Event {
  const event = new MouseEvent(type, {
    bubbles: true,
    cancelable: true,
    button: 0,
    clientX: x,
    clientY: y,
  })
  Object.defineProperties(event, {
    pointerType: { value: 'touch' },
    pointerId: { value: 7 },
    isPrimary: { value: true },
  })
  return event
}

function fixture() {
  const root = document.createElement('div')
  const strip = document.createElement('div')
  const tab = document.createElement('div')
  strip.className = 'dv-tabs-container'
  tab.className = 'dv-tab'
  strip.appendChild(tab)
  root.appendChild(strip)
  document.body.appendChild(root)
  Object.defineProperties(strip, {
    scrollWidth: { value: 600, configurable: true },
    clientWidth: { value: 200, configurable: true },
  })
  return { root, strip, tab }
}

describe('touch tab strip scroller', () => {
  it('scrolls horizontally with one finger and suppresses the trailing click', () => {
    const { root, strip, tab } = fixture()
    const activated = vi.fn()
    const clicked = vi.fn()
    tab.addEventListener('pointerdown', activated)
    tab.addEventListener('click', clicked)
    const dispose = installTouchTabStripScroller(root)

    tab.dispatchEvent(pointer('pointerdown', 140))
    tab.dispatchEvent(pointer('pointermove', 70))
    tab.dispatchEvent(pointer('pointerup', 70))
    tab.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }))

    expect(strip.scrollLeft).toBe(70)
    expect(activated).not.toHaveBeenCalled()
    expect(clicked).not.toHaveBeenCalled()
    dispose()
    root.remove()
  })

  it('preserves Dockview activation for a touch tap below the drag threshold', () => {
    const { root, tab } = fixture()
    const activated = vi.fn()
    tab.addEventListener('pointerdown', activated)
    const dispose = installTouchTabStripScroller(root)

    tab.dispatchEvent(pointer('pointerdown', 140))
    tab.dispatchEvent(pointer('pointermove', 136))
    tab.dispatchEvent(pointer('pointerup', 136))

    expect(activated).toHaveBeenCalledTimes(1)
    dispose()
    root.remove()
  })

  it('does not interfere when the tab strip has no horizontal overflow', () => {
    const { root, strip, tab } = fixture()
    Object.defineProperty(strip, 'scrollWidth', { value: 200 })
    const activated = vi.fn()
    tab.addEventListener('pointerdown', activated)
    const dispose = installTouchTabStripScroller(root)

    tab.dispatchEvent(pointer('pointerdown', 140))

    expect(activated).toHaveBeenCalledTimes(1)
    dispose()
    root.remove()
  })
})
