// Shared review popover. The popup is portaled to body so a horizontally scrollable toolbar cannot clip it.
import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

const POP: React.CSSProperties = {
  position: 'fixed', zIndex: 140, minWidth: 190,
  background: 'var(--fp-bp-tracing)', backdropFilter: 'var(--fp-bp-tracing-blur)',
  WebkitBackdropFilter: 'var(--fp-bp-tracing-blur)',
  border: '1px dashed var(--fp-border-strong)', borderRadius: 3,
  boxShadow: 'var(--fp-shadow-pop)', padding: 6,
}
const FOLD: React.CSSProperties = {
  position: 'absolute', top: 0, right: 0, width: 14, height: 14, pointerEvents: 'none',
  background: 'linear-gradient(225deg, var(--fp-bg) 0 48%, rgba(235,245,255,.35) 50%, transparent 62%)',
}

export function TraceMenu({ trigger, label, align = 'left', minWidth, children, onOpenChange }: {
  trigger: (open: boolean, toggle: () => void) => React.ReactNode
  label: string
  align?: 'left' | 'right'
  minWidth?: number
  children: React.ReactNode | ((close: () => void) => React.ReactNode)
  onOpenChange?: (open: boolean) => void
}) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLSpanElement | null>(null)
  const popRef = useRef<HTMLDivElement | null>(null)
  const [position, setPosition] = useState<React.CSSProperties>({ top: 0, left: 0, visibility: 'hidden' })
  const setBoth = (value: boolean) => { setOpen(value); onOpenChange?.(value) }

  const placePopup = useCallback(() => {
    const root = rootRef.current
    if (!root) return
    const rect = root.getBoundingClientRect()
    const popupRect = popRef.current?.getBoundingClientRect()
    const width = Math.max(minWidth ?? Number(POP.minWidth), popupRect?.width ?? 0)
    const height = popupRect?.height ?? 0
    const gutter = 8
    const left = align === 'right'
      ? Math.max(gutter, Math.min(window.innerWidth - width - gutter, rect.right - width))
      : Math.max(gutter, Math.min(window.innerWidth - width - gutter, rect.left))
    const below = rect.bottom + 6
    const top = height > 0 && below + height > window.innerHeight - gutter
      ? Math.max(gutter, rect.top - height - 6)
      : below
    setPosition({
      top,
      left,
      maxWidth: `calc(100vw - ${gutter * 2}px)`,
      maxHeight: `calc(100vh - ${gutter * 2}px)`,
      overflow: 'auto',
      visibility: 'visible',
    })
  }, [align, minWidth])

  useLayoutEffect(() => {
    if (open) placePopup()
  }, [open, placePopup])

  useEffect(() => {
    if (!open) return
    const onDown = (event: MouseEvent) => {
      if (event.target instanceof Node
        && !rootRef.current?.contains(event.target)
        && !popRef.current?.contains(event.target)) setBoth(false)
    }
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setBoth(false) }
    document.addEventListener('mousedown', onDown, true)
    document.addEventListener('keydown', onKey, true)
    window.addEventListener('resize', placePopup)
    window.addEventListener('scroll', placePopup, true)
    return () => {
      document.removeEventListener('mousedown', onDown, true)
      document.removeEventListener('keydown', onKey, true)
      window.removeEventListener('resize', placePopup)
      window.removeEventListener('scroll', placePopup, true)
    }
  }, [open, placePopup])

  return (
    <span ref={rootRef} style={{ position: 'relative', display: 'inline-flex', flex: 'none' }}>
      {trigger(open, () => setBoth(!open))}
      {open && createPortal(
        <div ref={popRef} style={{ ...POP, minWidth: minWidth ?? POP.minWidth, ...position }}
          role="group" aria-label={label}>
          <span style={FOLD} aria-hidden="true" />
          {typeof children === 'function' ? children(() => setBoth(false)) : children}
        </div>,
        document.body,
      )}
    </span>
  )
}
