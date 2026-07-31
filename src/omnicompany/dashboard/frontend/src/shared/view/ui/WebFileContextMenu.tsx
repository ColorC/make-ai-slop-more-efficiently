import React, { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Check, Copy, ExternalLink, FileText, Globe2, Link2 } from 'lucide-react'
import { copyText } from '../../../lib/copyText'
import {
  overlayFileWebUrl,
  type OverlayFileOpenTarget,
} from '../../../lib/overlayFileNavigation'

export interface WebFileMenuState {
  x: number
  y: number
  target: OverlayFileOpenTarget
}

const ST = {
  backdrop: {
    position: 'fixed', inset: 0, zIndex: 10020,
  } as React.CSSProperties,
  menu: {
    position: 'fixed', zIndex: 10021, minWidth: 228, maxWidth: 340,
    border: '1px solid var(--fp-border-strong)', borderRadius: 5,
    background: 'var(--fp-solid)', boxShadow: 'var(--fp-shadow-pop)',
    padding: 6, fontFamily: 'var(--fp-font-sans)',
  } as React.CSSProperties,
  title: {
    padding: '5px 9px 8px', color: 'var(--fp-text-3)', fontSize: 11,
    fontFamily: 'var(--fp-font-mono)', overflow: 'hidden',
    textOverflow: 'ellipsis', whiteSpace: 'nowrap',
  } as React.CSSProperties,
  item: {
    display: 'flex', alignItems: 'center', gap: 9, width: '100%',
    border: 0, borderRadius: 4, padding: '8px 9px', background: 'transparent',
    color: 'var(--fp-text)', textAlign: 'left', fontSize: 13, cursor: 'pointer',
  } as React.CSSProperties,
  divider: {
    height: 1, margin: '5px 3px', background: 'var(--fp-border)',
  } as React.CSSProperties,
}

type CopyState = 'idle' | 'ok' | 'fail'

export default function WebFileContextMenu({
  menu,
  onClose,
  onOpen,
}: {
  menu: WebFileMenuState | null
  onClose: () => void
  onOpen: (target: OverlayFileOpenTarget) => void
}) {
  const menuRef = useRef<HTMLDivElement>(null)
  const [position, setPosition] = useState({ left: 0, top: 0 })
  const [copyState, setCopyState] = useState<CopyState>('idle')

  useLayoutEffect(() => {
    if (!menu) return
    const rect = menuRef.current?.getBoundingClientRect()
    const width = rect?.width || 236
    const height = rect?.height || 250
    setPosition({
      left: Math.max(8, Math.min(menu.x, window.innerWidth - width - 8)),
      top: Math.max(8, Math.min(menu.y, window.innerHeight - height - 8)),
    })
  }, [menu])

  useEffect(() => {
    setCopyState('idle')
    if (!menu) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [menu, onClose])

  if (!menu || typeof document === 'undefined') return null
  const link = overlayFileWebUrl(menu.target)

  const copy = async (value: string) => {
    const ok = await copyText(value)
    setCopyState(ok ? 'ok' : 'fail')
    if (ok) window.setTimeout(onClose, 450)
  }

  const itemProps = {
    onMouseEnter: (event: React.MouseEvent<HTMLButtonElement>) => {
      event.currentTarget.style.background = 'var(--fp-bp-panel-hover)'
    },
    onMouseLeave: (event: React.MouseEvent<HTMLButtonElement>) => {
      event.currentTarget.style.background = 'transparent'
    },
  }

  return createPortal(
    <>
      <div
        style={ST.backdrop}
        data-omni-capture-ignore="true"
        onClick={onClose}
        onContextMenu={(event) => { event.preventDefault(); onClose() }}
      />
      <div
        ref={menuRef}
        role="menu"
        aria-label="文件操作"
        data-testid="web-file-context-menu"
        data-omni-capture-ignore="true"
        style={{ ...ST.menu, ...position }}
        onClick={(event) => event.stopPropagation()}
      >
        <div style={ST.title} title={menu.target.path}>{menu.target.name}</div>
        <button
          type="button"
          role="menuitem"
          data-testid="web-file-menu-open"
          style={ST.item}
          onClick={() => { onOpen(menu.target); onClose() }}
          {...itemProps}
        >
          <Globe2 size={15} />在 Dashboard 网页中打开
        </button>
        <button
          type="button"
          role="menuitem"
          style={ST.item}
          onClick={() => {
            window.open(link, '_blank', 'noopener,noreferrer')
            onClose()
          }}
          {...itemProps}
        >
          <ExternalLink size={15} />在新浏览器标签打开
        </button>
        <div style={ST.divider} />
        <button
          type="button"
          role="menuitem"
          data-testid="web-file-menu-copy-link"
          style={ST.item}
          onClick={() => { void copy(link) }}
          {...itemProps}
        >
          {copyState === 'ok' ? <Check size={15} /> : <Link2 size={15} />}
          {copyState === 'ok' ? '已复制' : copyState === 'fail' ? '复制失败' : '复制网页链接'}
        </button>
        <button
          type="button"
          role="menuitem"
          data-testid="web-file-menu-copy-path"
          style={ST.item}
          onClick={() => { void copy(menu.target.path) }}
          {...itemProps}
        >
          <Copy size={15} />复制文件路径
        </button>
        <button
          type="button"
          role="menuitem"
          style={ST.item}
          onClick={() => { void copy(menu.target.name) }}
          {...itemProps}
        >
          <FileText size={15} />复制文件名
        </button>
      </div>
    </>,
    document.body,
  )
}
