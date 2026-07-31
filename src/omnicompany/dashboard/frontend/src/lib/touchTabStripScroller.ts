export const TOUCH_TAB_DRAG_THRESHOLD_PX = 8

interface TouchTabDragState {
  pointerId: number
  strip: HTMLElement
  tab: HTMLElement | null
  startX: number
  startY: number
  startScrollLeft: number
  dragging: boolean
}

function touchPointer(event: PointerEvent): boolean {
  return event.isPrimary !== false && event.pointerType === 'touch'
}

/**
 * Adds single-finger horizontal scrolling to Dockview's custom tab scrollbar.
 * Dockview defaults to a .dv-scrollable wrapper whose inner tab list is
 * overflow:hidden, so wheel/trackpad input works while direct touch panning does not.
 */
export function installTouchTabStripScroller(root: HTMLElement): () => void {
  let drag: TouchTabDragState | null = null
  let suppressClickUntil = 0
  let suppressClickStrip: HTMLElement | null = null

  const onPointerDown = (event: PointerEvent) => {
    if (!touchPointer(event)) return
    const target = event.target
    if (!(target instanceof Element)) return
    const strip = target.closest<HTMLElement>('.dv-tabs-container')
    if (!strip || !root.contains(strip)) return
    if (strip.scrollWidth <= strip.clientWidth + 1) return

    drag = {
      pointerId: event.pointerId,
      strip,
      tab: target.closest<HTMLElement>('.dv-tab'),
      startX: event.clientX,
      startY: event.clientY,
      startScrollLeft: strip.scrollLeft,
      dragging: false,
    }

    try { strip.setPointerCapture?.(event.pointerId) } catch { /* old WebView */ }

    // Dockview activates tabs on pointerdown. Hold that action until pointerup so a
    // horizontal drag never changes the active tab merely because it started on one.
    event.stopPropagation()
  }

  const onPointerMove = (event: PointerEvent) => {
    if (!drag || event.pointerId !== drag.pointerId) return
    const dx = event.clientX - drag.startX
    const dy = event.clientY - drag.startY

    if (!drag.dragging) {
      if (Math.abs(dx) < TOUCH_TAB_DRAG_THRESHOLD_PX) return
      if (Math.abs(dy) >= Math.abs(dx)) {
        drag = null
        return
      }
      drag.dragging = true
    }

    event.preventDefault()
    event.stopPropagation()
    drag.strip.scrollLeft = drag.startScrollLeft - dx
  }

  const finish = (event: PointerEvent, cancelled: boolean) => {
    if (!drag || event.pointerId !== drag.pointerId) return
    const completed = drag
    drag = null
    try { completed.strip.releasePointerCapture?.(event.pointerId) } catch { /* old WebView */ }

    if (completed.dragging) {
      event.preventDefault()
      event.stopPropagation()
      suppressClickStrip = completed.strip
      suppressClickUntil = Date.now() + 450
      return
    }

    if (cancelled || !completed.tab) return

    // Re-emit a non-touch pointerdown only for a true tap. This preserves Dockview's
    // normal activation behavior while the original touch pointerdown remains blocked.
    const init: PointerEventInit = {
      bubbles: true,
      cancelable: true,
      button: 0,
      clientX: event.clientX,
      clientY: event.clientY,
      pointerType: 'mouse',
      isPrimary: true,
    }
    const activation = typeof PointerEvent === 'function'
      ? new PointerEvent('pointerdown', init)
      : new MouseEvent('pointerdown', init)
    completed.tab.dispatchEvent(activation)
  }

  const onPointerUp = (event: PointerEvent) => finish(event, false)
  const onPointerCancel = (event: PointerEvent) => finish(event, true)
  const onClick = (event: MouseEvent) => {
    if (!suppressClickStrip || Date.now() > suppressClickUntil) return
    const target = event.target
    if (!(target instanceof Node) || !suppressClickStrip.contains(target)) return
    event.preventDefault()
    event.stopPropagation()
    suppressClickStrip = null
    suppressClickUntil = 0
  }

  root.addEventListener('pointerdown', onPointerDown, true)
  root.addEventListener('pointermove', onPointerMove, true)
  root.addEventListener('pointerup', onPointerUp, true)
  root.addEventListener('pointercancel', onPointerCancel, true)
  root.addEventListener('click', onClick, true)

  return () => {
    root.removeEventListener('pointerdown', onPointerDown, true)
    root.removeEventListener('pointermove', onPointerMove, true)
    root.removeEventListener('pointerup', onPointerUp, true)
    root.removeEventListener('pointercancel', onPointerCancel, true)
    root.removeEventListener('click', onClick, true)
  }
}
