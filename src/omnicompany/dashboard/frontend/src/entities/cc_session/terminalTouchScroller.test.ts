import { describe, expect, it, vi } from 'vitest'
import {
  encodeTerminalWheel,
  installTerminalTouchScroller,
  isTerminalViewportAtBottom,
  terminalScrollTransport,
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

/** xterm stand-in for a full-screen TUI that took over mouse tracking. */
function mouseTrackingFixture(sgr = true) {
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
  const send = vi.fn()
  const handlers: Record<string, (params: (number | number[])[]) => boolean> = {}
  const term = {
    rows: 20,
    cols: 30,
    scrollLines,
    modes: { mouseTrackingMode: 'any' },
    parser: {
      registerCsiHandler: (
        id: { prefix?: string; final: string },
        callback: (params: (number | number[])[]) => boolean,
      ) => {
        handlers[id.final] = callback
        return { dispose: () => delete handlers[id.final] }
      },
    },
  }
  const dispose = installTerminalTouchScroller(root, term, { send })
  if (sgr) handlers.h?.([1000, 1006])
  return { root, screen, scrollLines, send, term, handlers, dispose }
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

  it('routes the drag to wheel reports when the CLI owns mouse tracking', () => {
    const { root, screen, scrollLines, send, dispose } = mouseTrackingFixture()

    screen.dispatchEvent(pointer('pointerdown', 100, 200))
    screen.dispatchEvent(pointer('pointermove', 101, 160))

    // 100px across a 300px/30-col screen is col 11; 160px of 400px/20 rows is row 9.
    expect(send).toHaveBeenCalledWith('\x1b[<65;11;9M\x1b[<65;11;9M')
    expect(scrollLines).not.toHaveBeenCalled()
    dispose()
    root.remove()
  })

  it('falls back to the legacy report encoding until the CLI asks for SGR', () => {
    const { root, screen, send, handlers, dispose } = mouseTrackingFixture(false)

    screen.dispatchEvent(pointer('pointerdown', 100, 100))
    screen.dispatchEvent(pointer('pointermove', 100, 140))
    // wheel-up button 96, col 11 -> 43 '+', row 8 -> 40 '(' in the legacy encoding.
    expect(send).toHaveBeenCalledWith('\x1b[M`+(\x1b[M`+(')

    handlers.h?.([1006])
    screen.dispatchEvent(pointer('pointermove', 100, 100))
    expect(send).toHaveBeenLastCalledWith('\x1b[<65;11;6M\x1b[<65;11;6M')
    dispose()
    root.remove()
  })

  it('keeps the viewport transport for CLIs that leave the mouse alone', () => {
    expect(terminalScrollTransport({ scrollLines: () => undefined })).toBe('viewport')
    expect(terminalScrollTransport({
      scrollLines: () => undefined,
      modes: { mouseTrackingMode: 'none' },
    })).toBe('viewport')
    expect(terminalScrollTransport({
      scrollLines: () => undefined,
      modes: { mouseTrackingMode: 'vt200' },
    })).toBe('mouse')
  })

  it('bounds one wheel burst the same way a viewport drag is bounded', () => {
    const report = encodeTerminalWheel(99, { col: 1, row: 1 }, true)
    expect(report.split('M').length - 1).toBe(TERMINAL_TOUCH_MAX_LINES_PER_MOVE)
    expect(encodeTerminalWheel(0, { col: 1, row: 1 }, true)).toBe('')
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
