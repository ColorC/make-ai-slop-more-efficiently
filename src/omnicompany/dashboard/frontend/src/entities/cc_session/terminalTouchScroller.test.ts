import { describe, expect, it, vi } from 'vitest'
import {
  installTerminalTouchScroller,
  isTerminalViewportAtBottom,
  TERMINAL_TOUCH_MAX_LINES_PER_MOVE,
} from './terminalTouchScroller'

function pointer(type: string, x: number, y: number): Event {
  const event = new MouseEvent(type, {
    bubbles: true,
    cancelable: true,
    clientX: x,
    clientY: y,
  })
  Object.defineProperties(event, {
    pointerType: { value: 'touch' },
    pointerId: { value: 9 },
    isPrimary: { value: true },
  })
  return event
}

function fixture() {
  const root = document.createElement('div')
  const screen = document.createElement('div')
  screen.className = 'xterm-screen'
  screen.getBoundingClientRect = () => ({
    x: 0, y: 0, top: 0, left: 0, right: 300, bottom: 400,
    width: 300, height: 400, toJSON: () => ({}),
  })
  root.appendChild(screen)
  document.body.appendChild(root)
  const scrollLines = vi.fn()
  const dispose = installTerminalTouchScroller(root, { rows: 20, scrollLines })
  return { root, screen, scrollLines, dispose }
}

describe('terminal touch scroller', () => {
  it('uses one-finger vertical drag to scroll xterm history in line increments', () => {
    const { root, screen, scrollLines, dispose } = fixture()

    screen.dispatchEvent(pointer('pointerdown', 100, 200))
    screen.dispatchEvent(pointer('pointermove', 101, 150))
    screen.dispatchEvent(pointer('pointermove', 101, 110))
    screen.dispatchEvent(pointer('pointerup', 101, 110))

    expect(scrollLines.mock.calls.flat()).toEqual([2, 2])
    dispose()
    root.remove()
  })

  it('scrolls toward older history when the finger moves down', () => {
    const { root, screen, scrollLines, dispose } = fixture()

    screen.dispatchEvent(pointer('pointerdown', 100, 100))
    screen.dispatchEvent(pointer('pointermove', 100, 145))

    expect(scrollLines).toHaveBeenCalledWith(-2)
    dispose()
    root.remove()
  })

  it('leaves taps and horizontal gestures to xterm/the surrounding UI', () => {
    const { root, screen, scrollLines, dispose } = fixture()
    const tapped = vi.fn()
    screen.addEventListener('click', tapped)

    screen.dispatchEvent(pointer('pointerdown', 100, 100))
    screen.dispatchEvent(pointer('pointermove', 104, 102))
    screen.dispatchEvent(pointer('pointerup', 104, 102))
    screen.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }))

    screen.dispatchEvent(pointer('pointerdown', 100, 100))
    screen.dispatchEvent(pointer('pointermove', 140, 105))

    expect(scrollLines).not.toHaveBeenCalled()
    expect(tapped).toHaveBeenCalledTimes(1)
    dispose()
    root.remove()
  })

  it('ignores small accidental movement and bounds a single large move', () => {
    const { root, screen, scrollLines, dispose } = fixture()

    screen.dispatchEvent(pointer('pointerdown', 100, 100))
    screen.dispatchEvent(pointer('pointermove', 102, 112))
    expect(scrollLines).not.toHaveBeenCalled()

    screen.dispatchEvent(pointer('pointermove', 102, 390))
    expect(scrollLines).toHaveBeenCalledWith(-TERMINAL_TOUCH_MAX_LINES_PER_MOVE)
    dispose()
    root.remove()
  })

  it('detects whether the latest terminal line is visible', () => {
    expect(isTerminalViewportAtBottom({
      buffer: { active: { viewportY: 80, baseY: 80 } },
      scrollLines: () => undefined,
    })).toBe(true)
    expect(isTerminalViewportAtBottom({
      buffer: { active: { viewportY: 12, baseY: 80 } },
      scrollLines: () => undefined,
    })).toBe(false)
  })
})
