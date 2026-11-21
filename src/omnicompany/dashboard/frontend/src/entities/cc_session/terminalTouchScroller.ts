export const TERMINAL_TOUCH_DRAG_THRESHOLD_PX = 16

export const TERMINAL_TOUCH_MAX_LINES_PER_MOVE = 12

export interface TouchScrollableTerminal {
  rows?: number
  cols?: number
  scrollLines: (lines: number) => void
  buffer?: {
    active?: {
      viewportY?: number
      baseY?: number
    }
  }
  modes?: { mouseTrackingMode?: string }
  parser?: {
    registerCsiHandler: (
      id: { prefix?: string; final: string },
      callback: (params: (number | number[])[]) => boolean,
    ) => { dispose: () => void }
  }
}

/** Where a touch drag has to land for the guest to actually scroll.
 *
 *  `viewport` — the CLI writes into the normal buffer, so its history *is*
 *  xterm's scrollback and the drag moves the viewport (codex / kimi /
 *  codebuddy, and Claude Code under the classic renderer).
 *  `mouse` — the CLI turned on mouse tracking (DECSET 1000/1002/1003) and
 *  scrolls its own transcript; xterm forwards a *wheel* to it, but nothing
 *  forwards a finger, so a tablet has no way to scroll at all. Measured on
 *  Claude Code 2.1 fullscreen renderer and on opencode.
 */
export type TerminalScrollTransport = 'viewport' | 'mouse'

export function terminalScrollTransport(
  term: TouchScrollableTerminal,
): TerminalScrollTransport {
  const mouse = term.modes?.mouseTrackingMode
  return mouse && mouse !== 'none' ? 'mouse' : 'viewport'
}

/** Wheel reports for `lines` at cell (col,row), both 1-based. Positive lines
 *  reveal newer content, i.e. wheel down (button 65). */
export function encodeTerminalWheel(
  lines: number,
  cell: { col: number; row: number },
  sgr: boolean,
): string {
  const count = Math.min(
    Math.abs(Math.trunc(lines)),
    TERMINAL_TOUCH_MAX_LINES_PER_MOVE,
  )
  if (count === 0) return ''
  const button = lines > 0 ? 65 : 64
  const col = Math.max(1, Math.trunc(cell.col))
  const row = Math.max(1, Math.trunc(cell.row))
  const report = sgr
    ? `\x1b[<${button};${col};${row}M`
    : `\x1b[M${String.fromCharCode(
      32 + button,
      32 + Math.min(col, 223),
      32 + Math.min(row, 223),
    )}`
  return report.repeat(count)
}

/** Follow DECSET/DECRST 1006. xterm publishes `modes.mouseTrackingMode` but not
 *  the report *encoding*, and a guest that only asked for 1000 cannot parse an
 *  SGR report — it would land in its prompt as garbage text. */
function trackSgrMouseEncoding(term: TouchScrollableTerminal): {
  enabled: () => boolean
  dispose: () => void
} {
  let enabled = false
  const parser = term.parser
  if (!parser?.registerCsiHandler) return { enabled: () => enabled, dispose: () => undefined }
  const watch = (final: string, value: boolean) =>
    parser.registerCsiHandler({ prefix: '?', final }, (params) => {
      for (const param of params) {
        const hit = Array.isArray(param) ? param.includes(1006) : param === 1006
        if (hit) enabled = value
      }
      // Deliberately unhandled: xterm still has to apply the mode itself.
      return false
    })
  const handles = [watch('h', true), watch('l', false)]
  return {
    enabled: () => enabled,
    dispose: () => handles.forEach((handle) => handle.dispose()),
  }
}

/** 1-based cell under the finger, for the coordinates a wheel report carries. */
function terminalCellAt(
  root: HTMLElement,
  term: TouchScrollableTerminal,
  event: PointerEvent,
): { col: number; row: number } {
  const cols = Math.max(1, term.cols || 80)
  const rows = Math.max(1, term.rows || 24)
  const rect = root.querySelector<HTMLElement>('.xterm-screen')?.getBoundingClientRect()
  if (!rect || rect.width <= 0 || rect.height <= 0) {
    return { col: Math.ceil(cols / 2), row: Math.ceil(rows / 2) }
  }
  const col = Math.floor(((event.clientX - rect.left) / rect.width) * cols) + 1
  const row = Math.floor(((event.clientY - rect.top) / rect.height) * rows) + 1
  return {
    col: Math.min(cols, Math.max(1, col)),
    row: Math.min(rows, Math.max(1, row)),
  }
}

interface TerminalTouchDragState {
  pointerId: number
  startX: number
  startY: number
  lastY: number
  remainderPx: number
  dragging: boolean
}

function isPrimaryTouch(event: PointerEvent): boolean {
  return event.pointerType === 'touch' && event.isPrimary !== false
}

export function isTerminalViewportAtBottom(term: TouchScrollableTerminal): boolean {
  const buffer = term.buffer?.active
  if (!buffer) return true
  return Number(buffer.viewportY || 0) >= Number(buffer.baseY || 0)
}

function terminalLineHeight(root: HTMLElement, term: TouchScrollableTerminal): number {
  const screen = root.querySelector<HTMLElement>('.xterm-screen')
  const rows = term.rows || 0
  const measured = screen?.getBoundingClientRect().height || 0
  if (rows > 0 && measured > 0) return measured / rows

  const fontSize = Number.parseFloat(getComputedStyle(root).fontSize || '')
  return Number.isFinite(fontSize) && fontSize > 0 ? fontSize * 1.2 : 18
}

/**
 * Converts single-finger vertical drags over xterm's canvas into scroll motion.
 * xterm handles wheel/trackpad scrolling itself, but direct touch panning is not
 * consistently exposed by Android WebView when the renderer owns the viewport.
 *
 * Where the drag goes depends on who owns scrolling (see
 * `terminalScrollTransport`): xterm's own scrollback for ordinary CLIs, or
 * synthesized wheel reports over `send` for a full-screen TUI that took over
 * mouse tracking — that one has no scrollback to move, so without this a
 * tablet cannot scroll it at all.
 */
export function installTerminalTouchScroller(
  root: HTMLElement,
  term: TouchScrollableTerminal,
  options: { send?: (data: string) => void } = {},
): () => void {
  let drag: TerminalTouchDragState | null = null
  let suppressClickUntil = 0
  const sgrMouse = trackSgrMouseEncoding(term)

  const onPointerDown = (event: PointerEvent) => {
    if (event.pointerType !== 'touch') return
    if (!isPrimaryTouch(event)) {
      drag = null
      return
    }
    drag = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      lastY: event.clientY,
      remainderPx: 0,
      dragging: false,
    }
  }

  const onPointerMove = (event: PointerEvent) => {
    if (!drag || event.pointerId !== drag.pointerId) return

    const totalX = event.clientX - drag.startX
    const totalY = event.clientY - drag.startY
    if (!drag.dragging) {
      if (Math.abs(totalY) < TERMINAL_TOUCH_DRAG_THRESHOLD_PX) return
      if (Math.abs(totalX) >= Math.abs(totalY)) {
        drag = null
        return
      }
      drag.dragging = true
      try { root.setPointerCapture?.(event.pointerId) } catch { /* old WebView */ }
    }

    event.preventDefault()
    event.stopPropagation()

    // Finger up reveals newer lines (positive xterm scroll); finger down reveals history.
    drag.remainderPx += drag.lastY - event.clientY
    drag.lastY = event.clientY

    const lineHeight = terminalLineHeight(root, term)
    const rawLines = Math.trunc(drag.remainderPx / lineHeight)
    if (rawLines === 0) return
    drag.remainderPx -= rawLines * lineHeight
    const lines = Math.max(
      -TERMINAL_TOUCH_MAX_LINES_PER_MOVE,
      Math.min(TERMINAL_TOUCH_MAX_LINES_PER_MOVE, rawLines),
    )
    if (options.send && terminalScrollTransport(term) === 'mouse') {
      const report = encodeTerminalWheel(
        lines,
        terminalCellAt(root, term, event),
        sgrMouse.enabled(),
      )
      if (report) options.send(report)
      return
    }
    term.scrollLines(lines)
  }

  const finish = (event: PointerEvent) => {
    if (!drag || event.pointerId !== drag.pointerId) return
    const completed = drag
    drag = null
    try { root.releasePointerCapture?.(event.pointerId) } catch { /* old WebView */ }
    if (!completed.dragging) return

    event.preventDefault()
    event.stopPropagation()
    suppressClickUntil = Date.now() + 450
  }

  const onClick = (event: MouseEvent) => {
    if (Date.now() > suppressClickUntil) return
    event.preventDefault()
    event.stopPropagation()
    suppressClickUntil = 0
  }

  root.addEventListener('pointerdown', onPointerDown, true)
  root.addEventListener('pointermove', onPointerMove, true)
  root.addEventListener('pointerup', finish, true)
  root.addEventListener('pointercancel', finish, true)
  root.addEventListener('click', onClick, true)

  return () => {
    sgrMouse.dispose()
    root.removeEventListener('pointerdown', onPointerDown, true)
    root.removeEventListener('pointermove', onPointerMove, true)
    root.removeEventListener('pointerup', finish, true)
    root.removeEventListener('pointercancel', finish, true)
    root.removeEventListener('click', onClick, true)
  }
}
