import React, { useRef } from 'react'

interface VProps {
  /** Current width of the panel being resized. Splitter caller manages state. */
  onResize: (delta: number) => void
  side?: 'left' | 'right'
}

/** Vertical bar (4px wide) used to drag horizontally — splits horizontal layout. */
export function VSplitter({ onResize, side = 'right' }: VProps) {
  const startX = useRef<number | null>(null)

  const onMouseDown = (e: React.MouseEvent) => {
    startX.current = e.clientX
    document.body.style.cursor = 'ew-resize'
    const onMove = (ev: MouseEvent) => {
      if (startX.current == null) return
      const dx = ev.clientX - startX.current
      // for right-side handle (sidebar): dragging right grows sidebar
      // for left-side handle (right panel): dragging right shrinks panel
      onResize(side === 'right' ? dx : -dx)
      startX.current = ev.clientX
    }
    const onUp = () => {
      startX.current = null
      document.body.style.cursor = ''
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  return (
    <div
      onMouseDown={onMouseDown}
      style={{
        width: 4, flexShrink: 0, cursor: 'ew-resize',
        background: '#1a1a1a', alignSelf: 'stretch',
      }}
      title="拖拽调整宽度"
    />
  )
}

interface HProps {
  onResize: (delta: number) => void
  /** Whether handle is at the top of the panel (true) or bottom. */
  side?: 'top' | 'bottom'
}

/** Horizontal bar (4px tall) used to drag vertically — splits vertical layout. */
export function HSplitter({ onResize, side = 'top' }: HProps) {
  const startY = useRef<number | null>(null)

  const onMouseDown = (e: React.MouseEvent) => {
    startY.current = e.clientY
    document.body.style.cursor = 'ns-resize'
    const onMove = (ev: MouseEvent) => {
      if (startY.current == null) return
      const dy = ev.clientY - startY.current
      // for top handle of bottom panel: dragging up grows bottom panel → return -dy
      onResize(side === 'top' ? -dy : dy)
      startY.current = ev.clientY
    }
    const onUp = () => {
      startY.current = null
      document.body.style.cursor = ''
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  return (
    <div
      onMouseDown={onMouseDown}
      style={{
        height: 4, flexShrink: 0, cursor: 'ns-resize',
        background: '#1a1a1a',
      }}
      title="拖拽调整高度"
    />
  )
}
