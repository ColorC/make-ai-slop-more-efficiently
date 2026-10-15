import React, { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { ExternalLink, PanelsTopLeft } from 'lucide-react'
import { openNativeBrowserUrl } from '../lib/workspaceNavigation'
import { NATIVE_WEBVIEW_OVERLAY_ATTRIBUTE } from '../lib/nativeWebviewOcclusion'
import {
  WEB_LINK_CONTEXT_MENU_EVENT,
  openWebTab,
  type WebLinkContextMenuDetail,
} from '../lib/webTabHost'

type LinkMenuState = WebLinkContextMenuDetail

const itemStyle: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 8, width: '100%', border: 0,
  borderRadius: 'var(--fp-r1)', padding: '7px 9px', background: 'transparent',
  color: 'var(--fp-text)', cursor: 'pointer', textAlign: 'left', fontSize: 'var(--fp-fs-3)',
}

/** Desktop link choice: the normal click stays workspace-first; right click makes the carrier explicit. */
export default function LinkContextMenu() {
  const [menu, setMenu] = useState<LinkMenuState | null>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const [position, setPosition] = useState({ left: 0, top: 0 })

  useEffect(() => {
    const onContextMenu = (event: MouseEvent) => {
      if (window.__OMNICOMPANY_HOST__?.runtime !== 'desktop') return
      if (!(event.target instanceof Element)) return
      const anchor = event.target.closest('a[href]') as HTMLAnchorElement | null
      if (!anchor || anchor.hasAttribute('download')) return
      event.preventDefault()
      setMenu({
        url: anchor.href,
        title: anchor.textContent?.trim() || anchor.getAttribute('aria-label') || anchor.href,
        x: event.clientX,
        y: event.clientY,
        baseHref: window.location.href,
      })
    }
    const onRequestedMenu = (event: Event) => {
      if (window.__OMNICOMPANY_HOST__?.runtime !== 'desktop') return
      setMenu((event as CustomEvent<WebLinkContextMenuDetail>).detail)
    }
    document.addEventListener('contextmenu', onContextMenu, true)
    document.addEventListener(WEB_LINK_CONTEXT_MENU_EVENT, onRequestedMenu)
    return () => {
      document.removeEventListener('contextmenu', onContextMenu, true)
      document.removeEventListener(WEB_LINK_CONTEXT_MENU_EVENT, onRequestedMenu)
    }
  }, [])

  useEffect(() => {
    if (!menu) return
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === 'Escape') setMenu(null) }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [menu])

  useLayoutEffect(() => {
    if (!menu) return
    const bounds = menuRef.current?.getBoundingClientRect()
    setPosition({
      left: Math.max(8, Math.min(menu.x, window.innerWidth - (bounds?.width || 220) - 8)),
      top: Math.max(8, Math.min(menu.y, window.innerHeight - (bounds?.height || 104) - 8)),
    })
  }, [menu])

  if (!menu || typeof document === 'undefined') return null
  const hover = {
    onMouseEnter: (event: React.MouseEvent<HTMLButtonElement>) => { event.currentTarget.style.background = 'var(--fp-bp-panel-hover)' },
    onMouseLeave: (event: React.MouseEvent<HTMLButtonElement>) => { event.currentTarget.style.background = 'transparent' },
  }
  return createPortal(<>
    <div style={{ position: 'fixed', inset: 0, zIndex: 10030 }}
      {...{ [NATIVE_WEBVIEW_OVERLAY_ATTRIBUTE]: 'true' }} onClick={() => setMenu(null)}
      onContextMenu={(event) => { event.preventDefault(); setMenu(null) }} />
    <div ref={menuRef} role="menu" aria-label="链接打开方式" data-testid="link-context-menu"
      style={{
        position: 'fixed', zIndex: 10031, left: position.left, top: position.top, minWidth: 220,
        maxWidth: 360, padding: 6, border: '1px solid var(--fp-border-strong)', borderRadius: 'var(--fp-r2)',
        background: 'var(--fp-solid)', boxShadow: 'var(--fp-shadow-pop)',
      }} onClick={(event) => event.stopPropagation()}>
      <div title={menu.url} style={{ padding: '4px 9px 7px', overflow: 'hidden', color: 'var(--fp-text-3)', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: 11 }}>
        {menu.title}
      </div>
      <button type="button" role="menuitem" data-testid="link-menu-open-workspace" style={itemStyle}
        onClick={() => {
          const opened = openWebTab(menu.url, menu.title, menu.baseHref, { allowExternal: true })
          if (!opened) openNativeBrowserUrl(menu.url)
          setMenu(null)
        }} {...hover}>
        <PanelsTopLeft size={14} />在内部页签打开
      </button>
      <button type="button" role="menuitem" data-testid="link-menu-open-system" style={itemStyle}
        onClick={() => { openNativeBrowserUrl(menu.url); setMenu(null) }} {...hover}>
        <ExternalLink size={14} />在系统浏览器打开
      </button>
    </div>
  </>, document.body)
}
