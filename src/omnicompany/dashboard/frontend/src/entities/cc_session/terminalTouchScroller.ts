export const TERMINAL_TOUCH_DRAG_THRESHOLD_PX = 16

export const TERMINAL_TOUCH_MAX_LINES_PER_MOVE = 12

export interface TouchScrollableTerminal {
  rows?: number
  scrollLines: (lines: number) => void
  buffer?: {
    active?: {
      viewportY?: number
      baseY?: number
    }
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
 * Converts single-finger vertical drags over xterm's canvas into scrollback lines.
 * xterm handles wheel/trackpad scrolling itself, but direct touch panning is not
 * consistently exposed by Android WebView when the renderer owns the viewport.
 */
export function installTerminalTouchScroller(
  root: HTMLElement,
  term: TouchScrollableTerminal,
): () => void {
  let drag: TerminalTouchDragState | null = null
  let suppressClickUntil = 0

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
    root.removeEventListener('pointerdown', onPointerDown, true)
    root.removeEventListener('pointermove', onPointerMove, true)
    root.removeEventListener('pointerup', finish, true)
    root.removeEventListener('pointercancel', finish, true)
    root.removeEventListener('click', onClick, true)
  }
}
